"""安全事件总线 (Security Event Bus) —— 统一安全审计流。

将安全引擎、网络守卫、MCP 安全加固器、分类器的所有决策汇聚到一个事件流,
实现:
1. **统一审计**: 所有安全决策都通过事件总线记录, 便于事后审计;
2. **实时告警**: 对 critical/high 级安全事件实时通知 UI;
3. **统计分析**: 聚合安全事件做趋势分析 (哪些命令最常被拦截);
4. **事件溯源**: 每个安全决策都有完整链路 (哪个层做了什么决策);
5. **可扩展**: 未来可接入外部 SIEM/监控系统。
"""

from __future__ import annotations

import atexit
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


# ============================================================ 事件类型

class SecurityEventType:
    """安全事件类型常量。"""

    # 命令级事件
    COMMAND_BLOCKED = "security.command.blocked"
    COMMAND_ALLOWED = "security.command.allowed"
    COMMAND_CONFIRMED = "security.command.confirmed"

    # 红线事件
    REDLINE_HIT = "security.redline.hit"
    HARD_REDLINE_HIT = "security.redline.hard_hit"

    # 网络事件
    NETWORK_BLOCKED = "security.network.blocked"
    NETWORK_CONFIRMED = "security.network.confirmed"

    # MCP 事件
    MCP_INJECTION_DETECTED = "security.mcp.injection"
    MCP_TOOL_REGISTERED = "security.mcp.tool_registered"

    # 分类器事件
    CLASSIFIER_DENY = "security.classifier.deny"
    CLASSIFIER_CONFIRM = "security.classifier.confirm"

    # 沙箱 (4 层滤网) 事件 —— 统一把沙箱裁决汇入审计流
    SANDBOX_BLOCKED = "security.sandbox.blocked"
    SANDBOX_ISOLATED = "security.sandbox.isolated"
    SANDBOX_CONFIRMED = "security.sandbox.confirmed"

    # 系统事件
    SECURITY_SUMMARY = "security.summary"


# ============================================================ 事件

@dataclass
class SecurityEvent:
    """一条安全事件。"""

    event_type: str
    timestamp: float
    payload: Dict[str, Any]
    source: str = ""  # 来源模块 (safety_engine / network_guard / mcp_guard / classifier)
    severity: str = "info"  # info / low / medium / high / critical

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
            "severity": self.severity,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


# ============================================================ 事件总线

class SecurityEventBus:
    """安全事件总线。

    用法:
        bus = SecurityEventBus()
        bus.on("security.command.blocked", lambda e: print(f"拦截: {e.payload}"))
        bus.emit(SecurityEvent(
            event_type=SecurityEventType.COMMAND_BLOCKED,
            timestamp=time.time(),
            payload={"command": "rm -rf /", "reason": "hard_redline"},
            source="safety_engine",
            severity="critical",
        ))
    """

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._handlers: Dict[str, List[Callable[[SecurityEvent], None]]] = {}
        self._wildcard_handlers: List[Callable[[SecurityEvent], None]] = []
        self._events: List[SecurityEvent] = []
        self._lock = threading.Lock()
        self._persist_path = persist_path
        # 实时告警回调: 对 critical/high 级安全事件实时通知 (桌面通知/蜂鸣等)。
        # 默认 None (不告警); 由交互式会话在启动时为总线注册一个 alerter。
        self._alerter: Optional[Callable[[SecurityEvent], None]] = None
        # 统计计数器
        self._counts: Dict[str, int] = {}
        self._severity_counts: Dict[str, int] = {}
        # 批量写入优化: 减少磁盘 I/O
        self._write_lock = threading.Lock()
        self._pending_writes: List[str] = []
        self._last_flush: float = time.time()

    def on(self, event_type: str, handler: Callable[[SecurityEvent], None]) -> None:
        """订阅安全事件。event_type 为 "*" 时匹配所有事件。"""
        with self._lock:
            if event_type == "*":
                self._wildcard_handlers.append(handler)
            else:
                self._handlers.setdefault(event_type, []).append(handler)

    def set_alerter(self, alerter: Optional[Callable[["SecurityEvent"], None]]) -> None:
        """注册实时告警回调 (桌面通知/蜂鸣等)。

        由交互式会话在启动时调用; 传入 None 可关闭告警。告警回调自身需保证
        线程安全且永不抛异常 (emit 会在锁外调用它并吞掉异常)。
        """
        self._alerter = alerter

    # 触发实时告警的事件类型 (只告警真正的安全决策, 避免噪音)
    _ALERT_EVENT_TYPES = frozenset({
        SecurityEventType.COMMAND_BLOCKED,
        SecurityEventType.COMMAND_ALLOWED,
        SecurityEventType.REDLINE_HIT,
        SecurityEventType.HARD_REDLINE_HIT,
        SecurityEventType.NETWORK_BLOCKED,
        SecurityEventType.MCP_INJECTION_DETECTED,
        SecurityEventType.CLASSIFIER_DENY,
        SecurityEventType.SANDBOX_BLOCKED,
    })

    def emit(self, event: SecurityEvent) -> None:
        """发射安全事件。"""
        with self._lock:
            self._events.append(event)
            # 保留最近 5000 条
            if len(self._events) > 5000:
                self._events = self._events[-2500:]

            # 更新统计
            self._counts[event.event_type] = self._counts.get(event.event_type, 0) + 1
            self._severity_counts[event.severity] = (
                self._severity_counts.get(event.severity, 0) + 1
            )

            # 快照 handler 列表 (锁内快照, 锁外派发)
            handlers = list(self._handlers.get(event.event_type, []))
            wildcards = list(self._wildcard_handlers)

        # 锁外派发
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                log.debug("安全事件处理器异常: %s", exc)
        for handler in wildcards:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                log.debug("安全事件通配处理器异常: %s", exc)

        # 实时告警: 对 critical/high 级安全决策实时通知 UI/桌面 (锁外派发, 吞异常)
        if (self._alerter is not None
                and event.event_type in self._ALERT_EVENT_TYPES
                and event.severity in ("critical", "high")):
            try:
                self._alerter(event)
            except Exception as exc:  # noqa: BLE001
                log.debug("安全告警回调异常: %s", exc)

        # 落盘 (如果配置了持久化路径)
        # 安全审计是产品核心承诺, 落盘必须保证完整性和失败可见。
        # 批量写入: 收集待写事件, 定期刷盘, 减少磁盘 I/O。
        # 但对 high/critical 级安全决策 (真正的红线拦截/破坏性操作) 立即刷盘,
        # 确保最关键的审计事件进程退出前已落盘, 避免依赖 atexit 或攒够条数。
        if self._persist_path is not None:
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
                # 使用独立的写锁避免阻塞事件派发
                with self._write_lock:
                    self._pending_writes.append(event.to_json())
                    now = time.time()
                    batch_ready = (
                        len(self._pending_writes) >= 10
                        or now - self._last_flush > 1.0
                    )
                    if batch_ready or event.severity in ("high", "critical"):
                        self._flush_to_disk()
            except Exception as exc:  # noqa: BLE001
                log.warning("安全审计落盘失败 (path=%s): %s", self._persist_path, exc)

    def emit_command_blocked(
        self, command: str, reason: str, source: str = "safety_engine"
    ) -> None:
        """快捷方法: 发射命令拦截事件。"""
        self.emit(SecurityEvent(
            event_type=SecurityEventType.COMMAND_BLOCKED,
            timestamp=time.time(),
            payload={"command": command[:500], "reason": reason},
            source=source,
            severity="high",
        ))

    def emit_command_allowed(
        self, command: str, source: str = "safety_engine"
    ) -> None:
        """快捷方法: 发射命令放行事件。"""
        self.emit(SecurityEvent(
            event_type=SecurityEventType.COMMAND_ALLOWED,
            timestamp=time.time(),
            payload={"command": command[:500]},
            source=source,
            severity="info",
        ))

    def emit_network_blocked(
        self, command: str, reasons: List[str]
    ) -> None:
        """快捷方法: 发射网络拦截事件。"""
        self.emit(SecurityEvent(
            event_type=SecurityEventType.NETWORK_BLOCKED,
            timestamp=time.time(),
            payload={"command": command[:500], "reasons": reasons},
            source="network_guard",
            severity="high",
        ))

    def emit_sandbox_blocked(
        self, command: str, layer: str, reason: str,
        severity: str = "critical", suggestions: Optional[List[str]] = None,
    ) -> None:
        """快捷方法: 发射沙箱滤网拦截事件 (L0-L3 任一滤网 deny)。"""
        self.emit(SecurityEvent(
            event_type=SecurityEventType.SANDBOX_BLOCKED,
            timestamp=time.time(),
            payload={
                "command": command[:500],
                "layer": layer,
                "reason": reason,
                "suggestions": suggestions or [],
            },
            source="sandbox",
            severity=severity,
        ))

    def emit_mcp_injection(
        self, tool_name: str, threats: List[Dict[str, str]], server: str = ""
    ) -> None:
        """快捷方法: 发射 MCP 注入检测事件。"""
        self.emit(SecurityEvent(
            event_type=SecurityEventType.MCP_INJECTION_DETECTED,
            timestamp=time.time(),
            payload={
                "tool_name": tool_name,
                "server": server,
                "threats": [t.get("description", "") for t in threats],
            },
            source="mcp_guard",
            severity="critical",
        ))

    # ---------------------------------------------------------- 查询

    def get_events(
        self,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[SecurityEvent]:
        """查询安全事件。"""
        with self._lock:
            events = self._events
            if event_type:
                events = [e for e in events if e.event_type == event_type]
            if severity:
                events = [e for e in events if e.severity == severity]
            return list(reversed(events[-limit:]))

    def get_stats(self) -> Dict[str, Any]:
        """返回安全事件统计。"""
        with self._lock:
            return {
                "total_events": len(self._events),
                "by_type": dict(self._counts),
                "by_severity": dict(self._severity_counts),
                "recent_critical": sum(
                    1 for e in self._events[-100:]
                    if e.severity == "critical"
                ),
            }

    def summary(self, last_n: int = 100) -> str:
        """生成安全事件摘要。"""
        stats = self.get_stats()
        lines = [
            f"# 安全事件摘要 (最近 {min(last_n, len(self._events))} 条)",
            "",
            f"- 总事件数: {stats['total_events']}",
            f"- 按类型: {json.dumps(stats['by_type'], ensure_ascii=False)}",
            f"- 按严重度: {json.dumps(stats['by_severity'], ensure_ascii=False)}",
            f"- 最近 critical: {stats['recent_critical']}",
            "",
        ]
        # 最近的 critical/high 事件
        recent = self.get_events(severity="critical", limit=10)
        if recent:
            lines.append("## 最近 Critical 事件")
            for e in recent:
                lines.append(f"- [{e.source}] {e.event_type}: {e.payload.get('command', '')[:100]}")
        return "\n".join(lines)

    def flush(self) -> None:
        """强制刷盘: 把缓冲区中的事件写入磁盘。

        在会话结束或关键安全操作后调用, 确保审计日志完整落盘。
        """
        if self._persist_path is not None:
            with self._write_lock:
                self._flush_to_disk()

    def shutdown(self) -> None:
        """关闭总线: 刷盘 + 清理资源。

        在进程退出前调用, 确保所有审计事件完整落盘。
        """
        self.flush()

    def _flush_to_disk(self) -> None:
        """内部方法: 把缓冲区写入磁盘。调用方需持有 _write_lock。"""
        if not self._pending_writes:
            return
        try:
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write("\n".join(self._pending_writes) + "\n")
                f.flush()
            self._pending_writes.clear()
            self._last_flush = time.time()
        except Exception as exc:  # noqa: BLE001
            log.warning("安全审计批量落盘失败 (path=%s): %s", self._persist_path, exc)
            # 单条失败不影响其他事件的后续写入

    def clear(self) -> None:
        """清空事件历史。"""
        with self._lock:
            self._events.clear()
            self._counts.clear()
            self._severity_counts.clear()


# ============================================================ 全局实例

_global_bus: Optional[SecurityEventBus] = None


def get_security_bus(persist_path: Optional[Path] = None) -> SecurityEventBus:
    """获取全局安全事件总线单例。

    默认开启落盘: 未显式传入 persist_path 时, 自动持久化到
    ``~/.qingxiaotuan/security-audit.jsonl``, 使任何会话 (含非交互式 / 构建内核)
    的安全事件都落盘可追溯, 而不是只存在于内存。

    若已存在单例但未设置持久化路径, 传入 persist_path 会补设 (方便调用方在创建后
    仍可开启审计落盘); 已有路径时不会被覆盖。
    """
    global _global_bus
    if _global_bus is None:
        if persist_path is None:
            try:
                from ..config.loader import home_dir
                persist_path = Path(home_dir()) / "security-audit.jsonl"
            except Exception:  # noqa: BLE001
                persist_path = None
        _global_bus = SecurityEventBus(persist_path=persist_path)
        # 进程退出时自动刷盘, 确保审计日志完整
        atexit.register(_global_bus.shutdown)
    elif persist_path is not None and _global_bus._persist_path is None:
        _global_bus._persist_path = persist_path
    return _global_bus
