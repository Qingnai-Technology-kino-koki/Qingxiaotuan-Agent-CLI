"""微内核 (Microkernel) —— 源自 DeepSeek Harness / Cordis 的理念。

内核本身不携带任何 Agent 能力, 只负责:
1. 插件的注册 / 激活 / 停用 / 依赖解析
2. 服务注册表 (插件之间通过服务名互相发现, 而非直接 import)
3. 生命周期事件钩子 (append-only 事件总线 + 中间件管线)

增强 (v0.3):
- @plugin 装饰器: 一行声明插件元数据 + activate/deactivate
- ServiceContainer: 懒工厂、类型安全访问、生命周期钩子
- MiddlewareEventBus: 中间件管线、事件拦截/修改、优先级排序
- 服务依赖自动注入 (activate 时按 require 声明自动注入)

公式: Model + Harness = Agent。模型负责思考, Harness 负责让思考可控地运行。
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Union

from .events import EventType as _EventType  # events 仅 TYPE_CHECKING 引用 kernel, 无循环导入

log = logging.getLogger(__name__)

# 事件历史上限: 只保留最近 N 条, 防止长生命周期内核内存无限增长。
_MAX_EVENTS = 2000

T = TypeVar("T")

# get() 快路径哨兵: 区分「服务不存在」与「服务值为 None」(占位服务可能提供 None)。
_MISSING = object()


class PluginError(Exception):
    pass


# =================================================================== 插件装饰器

def plugin(
    name: str,
    version: str = "0.1.0",
    provides: Optional[List[str]] = None,
    requires: Optional[List[str]] = None,
) -> Callable[[Type[Plugin]], Type[Plugin]]:
    """装饰器: 一行声明插件元数据, 无需手动设置类属性。

    用法::

        @plugin("my.service", provides=["my_svc"], requires=["model_adapter"])
        class MyService(Plugin):
            def activate(self, kernel):
                kernel.provide("my_svc", MyImpl(), owner=self.name)
    """

    def decorator(cls: Type[Plugin]) -> Type[Plugin]:
        cls.name = name
        cls.version = version
        cls.provides = list(provides or [])
        cls.requires = list(requires or [])
        return cls

    return decorator


# =================================================================== 插件基类

class Plugin:
    """插件基类。所有能力(工具、模型适配器、记忆、技能……)都是插件。

    子类既可以用类属性声明元数据::

        class MyPlugin(Plugin):
            name = "my.plugin"
            provides = ["my_service"]

    也可以通过构造参数传入, 或用 @plugin 装饰器。
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


# =================================================================== 服务容器

class ServiceContainer:
    """增强的服务注册表: 懒工厂、生命周期钩子、类型安全访问。

    与 Kernel.provide/require 向后兼容, 额外支持:
    - provide_factory(): 懒创建, 首次 require 时才实例化
    - 预定义服务钩子 (on_provide / on_require)
    - typed(): 类型安全的访问包装器
    """

    def __init__(self) -> None:
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._service_owner: Dict[str, str] = {}
        # 多贡献者增强叠加存储: service -> [(owner, placement, impl)]
        self._contributions: Dict[str, List[Tuple[str, str, Any]]] = {}
        self._on_provide: Dict[str, List[Callable[[str, Any], None]]] = {}
        self._on_require: Dict[str, List[Callable[[str, Any], None]]] = {}
        self._lock = threading.RLock()

    def provide(self, service: str, impl: Any, owner: str = "") -> None:
        """注册一个已实例化的服务。"""
        with self._lock:
            if service in self._services and service not in self._factories:
                raise PluginError(f"服务已被提供: {service} (by {self._service_owner[service]})")
            self._services[service] = impl
            self._factories.pop(service, None)  # 实例优先于工厂
            self._service_owner[service] = owner
        # 触发 on_provide 钩子 (锁外)
        for hook in self._on_provide.get(service, []):
            try:
                hook(service, impl)
            except Exception:  # noqa: BLE001
                pass

    def provide_factory(
        self, service: str, factory: Callable[[], Any], owner: str = ""
    ) -> None:
        """注册懒工厂: 首次 require 时才调用 factory() 创建实例。"""
        with self._lock:
            if service in self._services and service not in self._factories:
                raise PluginError(f"服务已被提供: {service}")
            self._factories[service] = factory
            self._service_owner[service] = owner

    def unprovide(self, service: str) -> None:
        with self._lock:
            self._services.pop(service, None)
            self._factories.pop(service, None)
            self._service_owner.pop(service, None)
            self._contributions.pop(service, None)

    def extend_service(
        self, service: str, impl: Any, owner: str = "", placement: str = "post"
    ) -> None:
        """以"增强叠加"方式提供服务: 同一服务可有多个贡献者, 不做替换。

        新架构核心语义 —— 内置提供"基础实现", 第三方通过 extend_service/provide_multi
        叠加增强 (pre 先于 / post 后于已有实现), 而非覆盖。

        placement: "base" 显式设为基础 / "pre" 提前 / "post" 追加。
        """
        if placement not in ("base", "pre", "post"):
            raise ValueError(f"非法 placement: {placement}")
        with self._lock:
            contribs = self._contributions.setdefault(service, [])
            if placement == "base":
                self._contributions[service] = [(owner, "base", impl)]
            elif not contribs:
                self._contributions[service].append((owner, "base", impl))
            elif placement == "pre":
                contribs.insert(0, (owner, "pre", impl))
            else:  # post
                contribs.append((owner, "post", impl))
            self._rebuild_service(service)

    def provide_multi(self, service: str, *contributors: Any, owner: str = "", placement: str = "post") -> None:
        """批量叠加多个贡献者到同一服务 (见 extend_service)。"""
        for i, impl in enumerate(contributors):
            pl = placement
            if placement == "post" and self._contributions.get(service):
                pl = "post"
            self.extend_service(service, impl, owner=f"{owner}#{i}" if owner else owner, placement=pl)

    def contributions(self, service: str) -> List[Tuple[str, str, Any]]:
        """取某服务的全部叠加贡献者: [(owner, placement, impl)]。"""
        with self._lock:
            return list(self._contributions.get(service, []))

    def _is_security(self, service: str) -> bool:
        return service.startswith("security") or "gate" in service

    def _rebuild_service(self, service: str) -> None:
        """根据贡献者列表重建该服务的对外实现。

        单贡献者 -> 直接返回该实例 (与 provide 行为一致)。
        多贡献者 -> 返回 AggregateProxy, 按 pre->base->post 顺序调用方法,
                   attribute 读取取首个贡献者 (基础优先)。
        """
        contribs = self._contributions.get(service, [])
        if not contribs:
            self._services.pop(service, None)
            self._service_owner.pop(service, None)
            return
        owners = [c[0] for c in contribs]
        order = (sorted([c for c in contribs if c[1] == "pre"], key=lambda c: c[0])
                 + [c for c in contribs if c[1] == "base"]
                 + sorted([c for c in contribs if c[1] == "post"], key=lambda c: c[0]))
        self._service_owner[service] = owners[0]
        if len(order) == 1:
            self._services[service] = order[0][2]
            return
        self._services[service] = AggregateProxy(order, on_fail="deny" if self._is_security(service) else "skip")

    def get(self, service: str, default: Any = None) -> Any:
        """获取服务, 不存在返回 default。

        快路径: 已实例化的服务无锁读取 (CPython dict 读是原子的) —— 工具分发等
        热循环每轮要取 config/registry 等服务, 避免每轮 RLock 往返。
        服务通常在启动期提供完毕, 运行期并发 provide 造成的短暂旧值可接受。
        """
        inst = self._services.get(service, _MISSING)
        if inst is _MISSING:
            with self._lock:
                if service in self._services:
                    inst = self._services[service]
                elif service in self._factories:
                    # 懒创建
                    inst = self._factories[service]()
                    self._services[service] = inst
                    self._factories.pop(service, None)
                else:
                    return default
        # 触发 on_require 钩子 (无钩子时零开销)
        hooks = self._on_require.get(service)
        if hooks:
            for hook in hooks:
                try:
                    hook(service, inst)
                except Exception:  # noqa: BLE001
                    pass
        return inst

    def require(self, service: str) -> Any:
        """获取服务, 不存在抛 PluginError。"""
        result = self.get(service)
        if result is None and service not in self._services and service not in self._factories:
            raise PluginError(f"所需服务不存在: {service}")
        return result

    def typed(self, service: str, typ: Type[T]) -> T:
        """类型安全的服务访问。"""
        inst = self.require(service)
        if not isinstance(inst, typ):
            raise PluginError(
                f"服务 {service} 类型不匹配: 期望 {typ.__name__}, "
                f"得到 {type(inst).__name__}"
            )
        return inst

    def on_provide(self, service: str, hook: Callable[[str, Any], None]) -> None:
        """注册服务提供钩子: 服务被 provide 时调用。"""
        self._on_provide.setdefault(service, []).append(hook)

    def on_require(self, service: str, hook: Callable[[str, Any], None]) -> None:
        """注册服务需求钩子: 服务被 require 时调用。"""
        self._on_require.setdefault(service, []).append(hook)

    @property
    def services(self) -> List[str]:
        with self._lock:
            all_svcs = set(self._services.keys()) | set(self._factories.keys())
            return sorted(all_svcs)

    @property
    def service_owners(self) -> Dict[str, str]:
        return dict(self._service_owner)


class AggregateProxy:
    """多贡献者服务的对外代理。

    顺序: pre -> base -> post (各层内按 owner 排序)。方法调用依次尝试,
    on_fail="deny" (安全服务) 时任一失败即抛错 (fail-closed); "skip" 时失败跳过
    下一个贡献者。attribute 读取取首个提供该属性的贡献者 (基础优先)。
    """

    def __init__(self, order: List[Tuple[str, str, Any]], on_fail: str = "skip") -> None:
        self._order = order
        self._on_fail = on_fail

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        for _owner, _placement, inst in self._order:
            attr = getattr(inst, name, None)
            if callable(attr):
                return self._make_invoker(name)
            if attr is not None:
                return attr
        raise AttributeError(name)

    def __getitem__(self, index: int) -> Any:
        return self._order[index][2]

    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self):
        for _o, _p, inst in self._order:
            yield inst

    def contributions_count(self) -> int:
        return len(self._order)

    def _make_invoker(self, name: str) -> Callable[..., Any]:
        def invoker(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for _owner, _placement, inst in self._order:
                fn = getattr(inst, name, None)
                if not callable(fn):
                    continue
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if self._on_fail == "deny":
                        log.error("聚合服务方法 %s 失败, fail-closed 拒行: %s", name, exc)
                        raise
                    log.warning("聚合服务方法 %s 失败, 跳过下一贡献者: %s", name, exc)
            if last_exc is not None:
                raise RuntimeError(f"聚合方法 {name} 全部贡献者均失败") from last_exc
            raise AttributeError(name)

        return invoker


# =================================================================== 中间件事件总线

@dataclass(order=True)
class Middleware:
    """事件中间件: 在事件派发前拦截/修改 payload。"""
    priority: int = 100  # 越小越先执行
    name: str = ""
    handler: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]] = field(
        default=lambda payload: payload
    )
    event_filter: Optional[List[str]] = field(default=None)
    # event_filter 为 None 表示匹配所有事件; 非空则只对列表中的事件生效


class MiddlewareEventBus:
    """增强事件总线: 支持中间件管线。

    中间件按 priority 排序 (越小越先), 可以:
    - 修改 payload (返回新的 payload)
    - 阻止事件派发 (返回 None)
    - 添加副作用 (日志、审计、指标)
    """

    def __init__(self) -> None:
        self._middleware: List[Middleware] = []
        self._middleware_lock = threading.Lock()

    def add_middleware(self, mw: Middleware) -> None:
        """添加中间件, 自动按 priority 排序。"""
        with self._middleware_lock:
            self._middleware.append(mw)
            self._middleware.sort(key=lambda m: m.priority)

    def remove_middleware(self, name: str) -> bool:
        """按名称移除中间件。"""
        with self._middleware_lock:
            before = len(self._middleware)
            self._middleware = [m for m in self._middleware if m.name != name]
            return len(self._middleware) < before

    def process(self, event_type: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """通过中间件管线处理 payload。返回 None 表示事件被阻止。"""
        result = dict(payload)
        for mw in self._middleware:
            # 检查事件过滤器
            if mw.event_filter and event_type not in mw.event_filter:
                continue
            try:
                output = mw.handler(result)
                if output is None:
                    return None  # 中间件阻止了事件
                result = output
            except Exception as exc:  # noqa: BLE001
                log.debug("中间件 %s 处理事件 %s 异常: %s", mw.name, event_type, exc)
        return result

    @property
    def middleware_names(self) -> List[str]:
        return [m.name for m in self._middleware]


# =================================================================== 内核事件

@dataclass
class KernelEvent:
    seq: int
    ts: float
    type: str
    payload: Dict[str, Any]


# =================================================================== 微内核

class Kernel:
    """青小团微内核。相当于 Cordis 的极简实现。

    v0.3 增强:
    - ServiceContainer 替代原始 dict, 支持懒工厂和生命周期钩子
    - MiddlewareEventBus 支持中间件管线
    - @plugin 装饰器简化插件声明
    """

    def __init__(self, on_event_overflow: Optional[Callable[[List[KernelEvent]], None]] = None) -> None:
        self._plugins: Dict[str, Plugin] = {}
        self._hooks: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._events: List[KernelEvent] = []
        self._lock = threading.RLock()
        self._seq = 0
        self._on_event_overflow = on_event_overflow
        # 溢出派发重入保护: 若 handler 内部再次 emit 导致新的溢出, 不再重复派发
        # overflow 事件, 避免 A->emit->overflow->A->emit... 无限递归级联。
        self._in_overflow = False
        # v0.3: 增强服务容器 + 中间件事件总线
        self.services = ServiceContainer()
        self.bus = MiddlewareEventBus()

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
                # 检查依赖 (同时检查旧 _services 和新 self.services)
                missing = []
                for s in plugin.requires:
                    if (s not in self.services._services
                            and s not in self.services._factories
                            and s not in self._plugins.get(name, Plugin()).provides):
                        # 也检查是否由其它插件在本次循环中提供
                        missing.append(s)
                # 简化: 如果 require 的服务在任何已激活插件的 provides 中, 且该插件已激活
                # 则认为已满足 (因为 activate 时会 provide)
                truly_missing = []
                for s in missing:
                    found = False
                    for aname, ap in self._plugins.items():
                        if aname in activated and s in ap.provides:
                            found = True
                            break
                    if not found:
                        truly_missing.append(s)
                if truly_missing:
                    continue
                plugin.activate(self)
                activated.add(name)
                del pending[name]
                progressed = True
                self.emit("plugin.activated", {"name": name})
            if not progressed:
                detail = {
                    n: [s for s in p.requires if s not in self.services._services]
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

    # ------------------------------------------------------------------ 服务 (兼容层 -> ServiceContainer)

    def provide(self, service: str, impl: Any, owner: str = "") -> None:
        """提供服务 (向后兼容)。"""
        self.services.provide(service, impl, owner)

    def unprovide(self, service: str) -> None:
        self.services.unprovide(service)

    def get(self, service: str, default: Any = None) -> Any:
        """获取服务 (向后兼容)。"""
        return self.services.get(service, default)

    def require(self, service: str) -> Any:
        """获取服务, 不存在抛异常 (向后兼容)。"""
        return self.services.require(service)

    # ------------------------------------------------------------------ 事件

    def on(self, event_type: Union[str, Any], handler: Callable[[Dict[str, Any]], None]) -> None:
        """订阅内核事件。

        event_type: EventType 枚举值 (推荐) 或字符串 (向后兼容); "*" 为通配。
        """
        key = event_type.value if isinstance(event_type, _EventType) else str(event_type)
        with self._lock:
            self._hooks.setdefault(key, []).append(handler)

    def emit(self, event_type: Union[str, Any], payload: Optional[Dict[str, Any]] = None) -> None:
        """发射内核事件 (经中间件管线处理)。

        event_type: EventType 枚举值 (推荐) 或字符串 (向后兼容)。
        payload: 事件负载, 建议使用 events.py 中定义的 TypedDict。
        """
        key = event_type.value if isinstance(event_type, _EventType) else str(event_type)
        # 中间件管线处理
        processed = self.bus.process(key, payload or {})
        if processed is None:
            return  # 中间件阻止了事件
        with self._lock:
            self._seq += 1
            event = KernelEvent(seq=self._seq, ts=time.time(), type=key,
                                payload=processed)
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
        # 溢出事件派发 (锁外, 避免递归 emit 导致级联截断; 带重入保护)
        if (
            overflow_handlers and overflow_payload is not None
            and not self._in_overflow
        ):
            self._in_overflow = True
            try:
                for handler in overflow_handlers:
                    try:
                        handler(overflow_payload)
                    except Exception as exc:  # noqa: BLE001
                        self._emit_error("event.overflow", exc)
            finally:
                self._in_overflow = False

    @staticmethod
    def _emit_error(event_type: str, exc: Exception) -> None:
        log.error("事件处理器异常 (type=%s): %s", event_type, exc)

    @property
    def events(self) -> List[KernelEvent]:
        return list(self._events)
