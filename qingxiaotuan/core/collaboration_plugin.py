"""协作插件 (Collaboration Plugin) —— 把多 Agent 协作子系统注册到微内核。

将 TaskDAG / specialized_roles / result_aggregator / collaboration_protocol
注册为内核服务, 供 Swarm / SubAgentPool / dispatch_tasks 等模块使用。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .kernel import Kernel, Plugin

log = logging.getLogger(__name__)


class CollaborationPlugin(Plugin):
    """多 Agent 协作子系统插件。"""

    name = "collaboration.core"
    provides = [
        "collaboration_protocol",
        "role_registry",
        "result_aggregator_factory",
    ]
    requires = ["config"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.require("config")

        # 1. 协作协议 (升级版黑板)
        try:
            from .collaboration_protocol import CollaborationProtocol
            protocol = CollaborationProtocol()
            kernel.provide("collaboration_protocol", protocol, owner=self.name)
            log.debug("协作协议已注册")
        except Exception as exc:  # noqa: BLE001
            log.warning("协作协议初始化失败 (降级跳过): %s", exc)

        # 2. 角色注册表
        try:
            from .specialized_roles import ROLES, get_role, list_roles, suggest_role
            role_registry = {
                "roles": ROLES,
                "get": get_role,
                "list": list_roles,
                "suggest": suggest_role,
            }
            kernel.provide("role_registry", role_registry, owner=self.name)
            log.debug("角色注册表已注册 (%d 个角色)", len(ROLES))
        except Exception as exc:  # noqa: BLE001
            log.warning("角色注册表初始化失败 (降级跳过): %s", exc)

        # 3. 结果聚合器工厂
        try:
            from .result_aggregator import ResultAggregator
            dedup_threshold = float(
                config.get("collaboration.dedup_threshold", 0.85)
            )
            conflict_threshold = float(
                config.get("collaboration.conflict_threshold", 0.3)
            )

            def _make_aggregator():
                return ResultAggregator(
                    dedup_threshold=dedup_threshold,
                    conflict_threshold=conflict_threshold,
                )

            kernel.provide("result_aggregator_factory", _make_aggregator, owner=self.name)
            log.debug("结果聚合器工厂已注册")
        except Exception as exc:  # noqa: BLE001
            log.warning("结果聚合器工厂初始化失败 (降级跳过): %s", exc)

    def deactivate(self, kernel: Kernel) -> None:
        for svc in self.provides:
            try:
                kernel.unprovide(svc)
            except Exception:  # noqa: BLE001
                pass
