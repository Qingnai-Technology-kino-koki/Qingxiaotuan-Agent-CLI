"""微内核 (Microkernel) —— 源自 DeepSeek Harness / Cordis 的理念。

内核本身不携带任何 Agent 能力, 只负责:
1. 插件的注册 / 激活 / 停用 / 依赖解析
2. 服务注册表 (插件之间通过服务名互相发现, 而非直接 import)
3. 生命周期事件钩子 (append-only 事件总线)

公式: Model + Harness = Agent。模型负责思考, Harness 负责让思考可控地运行。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


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

    def __init__(self) -> None:
        self._plugins: Dict[str, Plugin] = {}
        self._services: Dict[str, Any] = {}
        self._service_owner: Dict[str, str] = {}
        self._hooks: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._events: List[KernelEvent] = []
        self._seq = 0

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

    def on(self, event_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._hooks.setdefault(event_type, []).append(handler)

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self._seq += 1
        event = KernelEvent(seq=self._seq, ts=time.time(), type=event_type, payload=payload or {})
        self._events.append(event)
        # 异常隔离: 单个 handler (尤其是 UI 渲染/观测回调) 抛异常不应中断主循环。
        # 逐个捕获、记录, 继续派发给其余 handler。
        for handler in self._hooks.get(event_type, []):
            try:
                handler(event.payload)
            except Exception as exc:  # noqa: BLE001
                self._emit_error(event_type, exc)
        # 通配符 "*" handler 同样隔离
        if "*" in self._hooks:
            wide_payload = {"type": event_type, **event.payload}
            for handler in self._hooks["*"]:
                try:
                    handler(wide_payload)
                except Exception as exc:  # noqa: BLE001
                    self._emit_error("*", exc)

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
