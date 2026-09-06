"""安全插件 (Security Plugin) —— 把安全子系统注册到微内核。

将 security_classifier / network_guard / security_bus / mcp_security_guard
统一注册为内核服务, 其它模块通过 kernel.require("security_*") 获取,
而非各自维护全局单例。

设计原则:
- 单一注册点: 所有安全服务在 build_kernel() 时通过本插件一次性注册;
- 依赖注入: 安全组件通过内核服务注册表互相发现, 而非直接 import;
- 配置驱动: 可通过 config.yaml 的 security: 段开关各子系统;
- 异常隔离: 任一安全子系统初始化失败不影响其它子系统和主流程。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .kernel import Kernel, Plugin

log = logging.getLogger(__name__)


class SecurityPlugin(Plugin):
    """安全子系统插件: 注册分类器/网络守卫/事件总线/MCP加固器为内核服务。"""

    name = "security.core"
    provides = [
        "security_classifier",
        "network_guard",
        "security_bus",
        "mcp_security_guard",
        "security_auditor",
        "security_alerter",
        "file_integrity",
        "security_policy",
    ]
    requires = ["config"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.require("config")

        # 1. 安全分类器
        try:
            from .security_classifier import SecurityClassifier
            clf = SecurityClassifier()
            kernel.provide("security_classifier", clf, owner=self.name)
            log.debug("安全分类器已注册")
        except Exception as exc:  # noqa: BLE001
            log.warning("安全分类器初始化失败 (降级跳过): %s", exc)

        # 2. 网络守卫
        try:
            from .network_guard import NetworkGuard, set_network_guard
            # 从配置读取允许/禁止域名
            allowed = set(config.get("security.network.allowed_domains", []) or [])
            blocked = set(config.get("security.network.blocked_domains", []) or [])
            # 出口加固: CIDR 白名单 + 单向模式 (气隙/光闸场景)
            egress_cidrs = config.get("security.network.egress_cidr_allow", []) or []
            one_way = bool(config.get("security.network.one_way_mode", False))
            guard = NetworkGuard(
                allowed_domains=allowed or None,
                blocked_domains=blocked or None,
                deny_remote_exec=config.get("security.network.deny_remote_exec", True),
                deny_data_exfil=config.get("security.network.deny_data_exfil", True),
                egress_cidr_allow=egress_cidrs or None,
                one_way_mode=one_way,
            )
            kernel.provide("network_guard", guard, owner=self.name)
            # 注入全局单例, 使 tools.shell 执行路径真正使用配置好的守卫 (含 egress/one-way)
            set_network_guard(guard)
            log.debug("网络守卫已注册 (含 egress/one-way: cidr=%s one_way=%s)",
                       bool(egress_cidrs), one_way)
        except Exception as exc:  # noqa: BLE001
            log.warning("网络守卫初始化失败 (降级跳过): %s", exc)

        # 3. 安全事件总线
        try:
            from .security_bus import get_security_bus
            from pathlib import Path
            persist_path = config.home / "audit" / "security_events.jsonl"
            # 使用全局单例作为内核 security_bus, 确保审计器订阅与 shell._emitSecurityEvent
            # 指向同一实例, 安全事件不丢、审计落盘可追溯。
            bus = get_security_bus(persist_path=persist_path)
            kernel.provide("security_bus", bus, owner=self.name)
            log.debug("安全事件总线已注册 (落盘: %s)", persist_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("安全事件总线初始化失败 (降级跳过): %s", exc)

        # 4. MCP 安全加固器
        try:
            from ..tools.mcp.security import MCPSecurityGuard
            mcp_guard = MCPSecurityGuard(
                max_description_length=int(
                    config.get("security.mcp.max_description_length", 10000)
                ),
                audit_enabled=config.get("security.mcp.audit_enabled", True),
                block_on_injection=config.get("security.mcp.block_on_injection", True),
            )
            kernel.provide("mcp_security_guard", mcp_guard, owner=self.name)
            log.debug("MCP 安全加固器已注册")
        except Exception as exc:  # noqa: BLE001
            log.warning("MCP 安全加固器初始化失败 (降级跳过): %s", exc)

        # 5. 安全审计溯源器 (AEAD 加密审计日志, 可插拔加密后端)
        try:
            from .security_auditor import SecurityAuditor
            crypto_provider = config.get("security.crypto.provider", "software")
            auditor = SecurityAuditor(
                home=config.home,
                session_id=str(getattr(config, 'session_id', '')),
                crypto_provider=crypto_provider,
            )
            # 订阅 SecurityEventBus, 自动将安全事件写入审计日志
            bus_ref = kernel.get("security_bus")
            if bus_ref is not None:
                auditor.subscribe_bus(bus_ref)
            kernel.provide("security_auditor", auditor, owner=self.name)
            log.debug("安全审计溯源器已注册 (AEAD 加密)")
        except Exception as exc:  # noqa: BLE001
            log.warning("安全审计溯源器初始化失败 (降级跳过): %s", exc)

        # 6. 安全告警器 (实时桌面通知 + 日志告警)
        try:
            from .security_alert import SecurityAlerter
            alerter = SecurityAlerter(
                home=config.home,
                enable_desktop=config.get("security.alert.desktop", True),
            )
            # 订阅 SecurityEventBus, 自动将高危事件转为桌面通知
            bus_ref2 = kernel.get("security_bus")
            if bus_ref2 is not None:
                alerter.subscribe_bus(bus_ref2)
            kernel.provide("security_alerter", alerter, owner=self.name)
            log.debug("安全告警器已注册")
        except Exception as exc:  # noqa: BLE001
            log.warning("安全告警器初始化失败 (降级跳过): %s", exc)

        # 7. 文件完整性监控 (关键文件防篡改)
        try:
            from .file_integrity import FileIntegrityMonitor
            fim = FileIntegrityMonitor(home=config.home)
            kernel.provide("file_integrity", fim, owner=self.name)
            log.debug("文件完整性监控已注册")
        except Exception as exc:  # noqa: BLE001
            log.warning("文件完整性监控初始化失败 (降级跳过): %s", exc)

        # 8. 安全策略引擎 (YAML 声明式规则)
        try:
            from .security_policy import SecurityPolicyEngine
            policy_engine = SecurityPolicyEngine(home=config.home)
            kernel.provide("security_policy", policy_engine, owner=self.name)
            log.debug("安全策略引擎已注册 (%d 条规则)", len(policy_engine.list_rules()))
        except Exception as exc:  # noqa: BLE001
            log.warning("安全策略引擎初始化失败 (降级跳过): %s", exc)

    def deactivate(self, kernel: Kernel) -> None:
        """停用时清理。"""
        for svc in self.provides:
            try:
                kernel.unprovide(svc)
            except Exception:  # noqa: BLE001
                pass
