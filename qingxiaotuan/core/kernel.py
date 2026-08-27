"""微内核 (Microkernel) —— 源自 DeepSeek Harness / Cordis 的理念。

内核本身不携带任何 Agent 能力, 只负责:
1. 插件的注册 / 激活 / 停用 / 依赖解析
2. 服务注册表 (插件之间通过服务名互相发现, 而非直接 import)
3. 生命周期事件钩子 (append-only 事件总线)

公式: Model + Harness = Agent。模型负责思考, Harness 负责让思考可控地运行。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union

# 事件历史上限: 只保留最近 N 条, 防止长生命周期内核内存无限增长。
_MAX_EVENTS = 2000


class PluginError(Exception):
    pass


class Plugin:
    """插件基类。所有能力(工具、模型适配器、记忆、技能……)都是插件。

    子类既可以用类属性声明元数据:
        class MyPlugin(Plugin):
            name = "my.plugin"
            provides = ["my_service"]
    也可以通过构造参数传入。
    """

    name: str = ""
    version: str = "0.1.0"
    provides: List[str] = []   # 提供的服务名
    requires: List[str] = []   # 依赖的服务名

    def __init__(self, name: str = "", version: str = "",
                 provides: Optional[List[str]] = None,
                 requires: Optional[List[str]] = None) -> None:
        if name:
            self.name = name
        if version:
            self.version = version
        if provides is not None:
            self.provides = list(provides)
        else:
            self.provides = list(type(self).provides)
        if requires is not None:
            self.requires = list(requires)
        else:
            self.requires = list(type(self).requires)

    def activate(self, kernel: "Kernel") -> None:
        """插件被激活时调用, 通常在此向内核注册服务。"""

    def deactivate(self, kernel: "Kernel") -> None:
        """插件被停用时调用。"""


@dataclass
class KernelEvent:
    seq: int
    ts: float
    type: str
    payload: Dict[str, Any]


class Kernel:
    """青小团微内核。相当于 Cordis 的极简实现。"""

    def __init__(self, on_event_overflow: Optional[Callable[[List[KernelEvent]], None]] = None) -> None:
        self._plugins: Dict[str, Plugin] = {}
        self._services: Dict[str, Any] = {}
        self._service_owner: Dict[str, str] = {}
        self._hooks: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._events: List[KernelEvent] = []
        self._lock = threading.RLock()
        self._seq = 0
        self._on_event_overflow = on_event_overflow

    # ------------------------------------------------------------------ 插件

    def register(self, plugin: Plugin) -> None:
        if not plugin.name:
            raise PluginError("插件缺少 name")
        if plugin.name in self._plugins:
            raise PluginError(f"插件重复注册: {plugin.name}")
        self._plugins[plugin.name] = plugin
        self.emit("plugin.registered", {"name": plugin.name, "version": plugin.version})

    def activate_all(self) -> None:
        """按依赖拓扑序激活全部插件。"""
        activated: set[str] = set()
        pending = dict(self._plugins)
        guard = 0
        while pending:
            guard += 1
            if guard > 1000:
                raise PluginError("插件依赖存在环, 无法激活")
            progressed = False
            for name, plugin in list(pending.items()):
                missing = [s for s in plugin.requires if s not in self._services]
                if missing:
                    continue
                plugin.activate(self)
                activated.add(name)
                del pending[name]
                progressed = True
                self.emit("plugin.activated", {"name": name})
            if not progressed:
                detail = {
                    n: [s for s in p.requires if s not in self._services]
                    for n, p in pending.items()
                }
                raise PluginError(f"插件依赖无法满足: {detail}")

    def deactivate_all(self) -> None:
        for plugin in reversed(list(self._plugins.values())):
            try:
                plugin.deactivate(self)
                self.emit("plugin.deactivated", {"name": plugin.name})
            except Exception as exc:  # noqa: BLE001
                self.emit("plugin.error", {"name": plugin.name, "error": str(exc)})

    @property
    def plugins(self) -> Dict[str, Plugin]:
        return dict(self._plugins)

    # ------------------------------------------------------------------ 服务

    def provide(self, service: str, impl: Any, owner: str = "") -> None:
        if service in self._services:
            raise PluginError(f"服务已被提供: {service} (by {self._service_owner[service]})")
        self._services[service] = impl
        self._service_owner[service] = owner
        self.emit("service.provided", {"service": service, "owner": owner})

    def unprovide(self, service: str) -> None:
        self._services.pop(service, None)
        self._service_owner.pop(service, None)

    def get(self, service: str, default: Any = None) -> Any:
        return self._services.get(service, default)

    def require(self, service: str) -> Any:
        if service not in self._services:
            raise PluginError(f"所需服务不存在: {service}")
        return self._services[service]

    @property
    def services(self) -> List[str]:
        return sorted(self._services)

    # ------------------------------------------------------------------ 事件

    def on(self, event_type: Union[str, Any], handler: Callable[[Dict[str, Any]], None]) -> None:
        """订阅内核事件。

        event_type: EventType 枚举值 (推荐) 或字符串 (向后兼容); "*" 为通配。
        """
        from .events import EventType as _ET
        key = event_type.value if isinstance(event_type, _ET) else str(event_type)
        with self._lock:
            self._hooks.setdefault(key, []).append(handler)

    def emit(self, event_type: Union[str, Any], payload: Optional[Dict[str, Any]] = None) -> None:
        """发射内核事件。

        event_type: EventType 枚举值 (推荐) 或字符串 (向后兼容)。
        payload: 事件负载, 建议使用 events.py 中定义的 TypedDict。
        """
        from .events import EventType as _ET
        key = event_type.value if isinstance(event_type, _ET) else str(event_type)
        with self._lock:
            self._seq += 1
            event = KernelEvent(seq=self._seq, ts=time.time(), type=key,
                                payload=payload or {})
            self._events.append(event)
            overflow_handlers: list = []
            if len(self._events) > _MAX_EVENTS:
                overflow = len(self._events) - _MAX_EVENTS
                evicted = list(self._events[:overflow])
                del self._events[:overflow]
                # 通知溢出回调 (审计/观测方可以记录被丢弃的事件)
                if self._on_event_overflow is not None:
                    try:
                        self._on_event_overflow(evicted)
                    except Exception:  # noqa: BLE001
                        pass
                # 收集溢出事件的 handlers (在锁内快照, 锁外派发, 避免递归 emit)
                overflow_handlers = list(self._hooks.get("event.overflow", []))
                overflow_handlers += list(self._hooks.get("*", []))
                overflow_payload = {"evicted": len(evicted), "remaining": len(self._events)}
            else:
                overflow_payload = None
            # 复制 handler 列表再派发, 避免派发期间注册/卸载导致迭代器失效。
            handlers = list(self._hooks.get(key, []))
            wildcard = list(self._hooks.get("*", []))
        # 异常隔离: 单个 handler (尤其是 UI 渲染/观测回调) 抛异常不应中断主循环。
        # 逐个捕获、记录, 继续派发给其余 handler。
        for handler in handlers:
            try:
                handler(event.payload)
            except Exception as exc:  # noqa: BLE001
                self._emit_error(key, exc)
        # 通配符 "*" handler 同样隔离
        if wildcard:
            wide_payload = {"type": key, **event.payload}
            for handler in wildcard:
                try:
                    handler(wide_payload)
                except Exception as exc:  # noqa: BLE001
                    self._emit_error("*", exc)
        # 溢出事件派发 (锁外, 避免递归 emit 导致级联截断)
        if overflow_handlers and overflow_payload is not None:
            for handler in overflow_handlers:
                try:
                    handler(overflow_payload)
                except Exception as exc:  # noqa: BLE001
                    self._emit_error("event.overflow", exc)

    @staticmethod
    def _emit_error(event_type: str, exc: Exception) -> None:
        # 避免循环 import: 仅做最轻量记录
        try:
            import logging
            logging.getLogger("qingxiaotuan.kernel").error(
                "事件处理器异常 (type=%s): %s", event_type, exc
            )
        except Exception:
            pass

    @property
    def events(self) -> List[KernelEvent]:
        return list(self._events)
