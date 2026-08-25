"""审计插件 —— 通配订阅内核事件总线, 把模型/工具/权限/任务生命周期事件归档。

仅做"观测录影", 不修改任何事件语义: 业务模块照常 emit, 审计 plugin 在激活时
向内核注册一个 "*" 通配订阅, 所有事件经脱敏后落盘 + 入环形缓冲。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..core.kernel import Kernel, Plugin
from .store import AuditStore


class AuditPlugin(Plugin):
    name = "audit"
    provides = ["audit_store"]
    requires: list = []

    def __init__(self) -> None:
        self.store: AuditStore | None = None

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        home = config.home if config is not None else Path.home() / ".qingxiaotuan"
        enabled = bool(config.get("audit.enabled", True)) if config is not None else True
        persist = bool(config.get("audit.persist", True)) if config is not None else True
        # 业务敏感事件 (脱敏后) 才值得记录; 纯 UI 渲染事件噪声大, 过滤掉。
        self._skip_types = {"turn.step", "system.stable", "kernel.emit_error"}
        self.store = AuditStore(home=home, enabled=enabled, persist=persist)
        kernel.provide("audit_store", self.store, owner=self.name)
        # 通配订阅: 所有内核事件自动归档 (异常隔离在 kernel.emit 内已保证)
        kernel.on("*", self._on_event)
        kernel.emit("audit.ready", {"enabled": enabled, "persist": persist})

    def _on_event(self, payload: Dict[str, Any]) -> None:
        if self.store is None:
            return
        event_type = payload.get("type") if "type" in payload else None
        # 通配 handler 收到的 payload 已含 type 字段; 若是直接事件则取包一层
        if event_type is None:
            # emit("*", {...}) 路径: payload 即原始事件 dict
            event_type = "event"
            data = payload
        else:
            # emit("tool.executed", {...}) 路径经通配后变 {type, ...}
            data = payload
        if event_type in self._skip_types:
            return
        self.store.log(event_type, data)
