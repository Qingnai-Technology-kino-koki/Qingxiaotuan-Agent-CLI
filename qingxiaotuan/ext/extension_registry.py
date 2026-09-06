"""分层扩展注册表 —— 第三方热插拔增强的统一入口。

设计目标 (对齐新架构):
- 内置模块注册为 "builtin" 基础层; 第三方插件注册为 "third_party" 增强层。
- 同一能力 key 可以有多个贡献者, 按 (layer, priority) 排序叠加, 不做替换。
- placement 区分 pre(前置包装, 先于内置) / post(后置包装, 后于内置) / replace(整体覆盖)。
  默认只允许 pre/post 叠加; replace 需显式声明且不得用于安全类能力。
- fail-closed: 安全增强 (kind="security") 在第三方崩溃时按 on_error 策略处理,
  默认 "deny"(抛错拒行, 绝不静默放行), 能力增强默认 "degrade"(回退基础实现)。
- 发现来源:
  1. `[project.entry-points."qxt.extensions"]` (pyproject 声明)
  2. `QXT_EXT_PATH` 环境变量指向的目录 (扫描 *.py 中注册的扩展)
  3. 运行时 `register()` 直接注册 (第三方包初始化时调用)

不并存原则: 内置 9 引擎迁到本注册表后, 旧的硬编码 ENGINE_MAP 由注册表等价生成
(见 ext/registry.py 迁移), 不再维护第二套注册逻辑。
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "qxt.extensions"


class Layer(str, Enum):
    BUILTIN = "builtin"
    THIRD_PARTY = "third_party"


class Placement(str, Enum):
    BASE = "base"      # 基础实现
    PRE = "pre"        # 前置包装: 在基础实现前执行
    POST = "post"      # 后置包装: 在基础实现后执行
    REPLACE = "replace"  # 整体覆盖 (禁止用于 security 能力)


class OnError(str, Enum):
    DEGRADE = "degrade"  # 第三方报错时降级到基础实现 (能力增强, 默认)
    DENY = "deny"        # 第三方报错时抛错拒行 (安全增强 fail-closed)


@dataclass(order=True)
class Contributor:
    """一个能力 key 下的一个贡献者。"""
    name: str
    layer: Layer
    placement: Placement
    priority: int
    impl: Any = None                      # 已实例化的实现
    factory: Optional[Callable[[], Any]] = None  # 懒工厂
    kind: str = "capability"              # "security" / "capability"

    def runtime(self) -> Any:
        if self.impl is not None:
            return self.impl
        if self.factory is not None:
            self.impl = self.factory()
            return self.impl
        raise AttributeError(f"扩展 {self.name} 没有可运行实现")


@dataclass
class Resolved:
    """一个能力 key 解析后的可调用链。"""
    name: str
    on_error: OnError
    base: Optional[Contributor] = None
    pre: List[Contributor] = field(default_factory=list)   # 已按 priority 排序
    post: List[Contributor] = field(default_factory=list)  # 已按 priority 排序

    def is_empty(self) -> bool:
        return self.base is None and not self.pre and not self.post

    def pluck(self, attr: str) -> List[Any]:
        """收集某属性在 pre→base→post 顺序下的运行实例。"""
        chain: List[Any] = []
        for c in self.pre:
            chain.append(getattr(c.runtime(), attr))
        if self.base is not None:
            chain.append(getattr(self.base.runtime(), attr))
        for c in self.post:
            chain.append(getattr(c.runtime(), attr))
        return chain


class ExtensionRegistry:
    """分层扩展注册表。线程安全。"""

    def __init__(self, on_error: OnError = OnError.DEGRADE) -> None:
        self._contrib: Dict[str, List[Contributor]] = {}
        self._default_on_error: OnError = on_error
        self._lock = threading.RLock()
        self._auto_loaded = False
        self._load_lock = threading.Lock()

    # --------------------------------------------------------- register

    def register(
        self,
        name: str,
        impl: Any = None,
        *,
        layer: str = "third_party",
        placement: str = "auto",
        priority: int = 100,
        kind: str = "capability",
        owner: str = "",
        factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """注册一个能力贡献者。

        参数:
            name: 能力 key (如 "safety.score", "security_gate.pre_verdict")
            impl: 已实例化的增强实现 (与 factory 二选一)
            layer: "builtin" / "third_party"
            placement: "auto"(内置默认 base, 第三方默认 post) /
                       "base" / "pre" / "post" / "replace"
            priority: 数字越小越先执行 (同层内排序)
            kind: "security"(fail-closed) / "capability"(允许降级)
            owner: 归属标签 (插件名), 便于审计
            factory: 懒工厂, 首次解析才实例化
        """
        if placement == "auto":
            placement = "base" if layer == "builtin" else "post"
        if placement == Placement.REPLACE and kind == "security":
            raise ValueError(f"security 能力 {name} 不允许 replace 覆盖 (须整体 fail-closed)")
        if placement != Placement.REPLACE and impl is None and factory is None:
            raise ValueError(f"扩展 {name} 的 pre/post 必须提供 impl 或 factory")
        try:
            layer_e = Layer(layer)
            placement_e = Placement(placement)
        except ValueError as exc:  # 拒绝非法取值
            raise ValueError(f"非法 layer/placement: {layer}/{placement}") from exc

        if placement_e == Placement.REPLACE and impl is None and factory is None and layer_e == Layer.THIRD_PARTY:
            raise ValueError(f"第三方 replace 扩展 {name} 必须提供实现; 只有内置可声明 replace 位")

        contrib = Contributor(
            name=f"{owner}.{name}" if owner else name,
            layer=layer_e,
            placement=placement_e,
            priority=priority,
            impl=impl,
            factory=factory,
            kind=kind,
        )
        with self._lock:
            # replace (内置) 直接设为该 key 的唯一基础
            if placement_e == Placement.REPLACE:
                self._contrib[name] = [
                    c for c in self._contrib.get(name, []) if c.placement != Placement.REPLACE
                ]
                self._contrib[name].append(contrib)
            else:
                self._contrib.setdefault(name, []).append(contrib)
        log.debug("已注册扩展能力: %s (layer=%s placement=%s priority=%d)",
                  name, layer_e.value, placement_e.value, priority)

    # ------------------------------------------------------- resolution

    def contributors(self, name: str) -> List[Contributor]:
        """返回某个能力 key 排序后的全部贡献者。"""
        with self._lock:
            contribs = list(self._contrib.get(name, []))
        _sort(contribs)
        return contribs

    def resolve(self, name: str, on_error: Optional[OnError] = None) -> Resolved:
        """把某个能力 key 解析为叠加链。

        - base = layer==builtin 且 placement in (base/replace) 的贡献者 (取 priority 最小)
        - pre  = layer==third_party 且 placement==pre (外加 builtin pre 也允许)
        - post = layer==third_party 且 placement==post
        """
        self._autoload()
        contribs = self.contributors(name)
        pre: List[Contributor] = []
        base: Optional[Contributor] = None
        post: List[Contributor] = []
        for c in contribs:
            if c.placement in (Placement.REPLACE, Placement.BASE):
                base = c
            elif c.placement == Placement.PRE:
                pre.append(c)
            elif c.placement == Placement.POST:
                post.append(c)
            elif c.layer == Layer.BUILTIN and base is None:
                # 默认位 (无显式 placement 的内置) 视为基础实现
                base = c
        # 若全部贡献者都是 third_party pre/post 且无内置基础, 首个 post 兜底为基础
        if base is None and pre and not post:
            base = pre.pop(0)
        return Resolved(
            name=name,
            on_error=on_error or self._default_on_error,
            base=base,
            pre=pre,
            post=post,
        )

    def names(self) -> List[str]:
        """列出全部已注册能力 key。"""
        with self._lock:
            return sorted(self._contrib.keys())

    # -------------------------------------------- chain invocation / guard

    def invoke(self, name: str, method: str, *args: Any, **kwargs: Any) -> List[Any]:
        """按 pre→base→post 顺序调用某方法, 返回各贡献者该方法的结果列表。

        用于"增强链"语义: 每个贡献者都执行同一方法, 结果聚合返回。
        """
        resolved = self.resolve(name)
        results: List[Any] = []
        for c in (resolved.pre + ([resolved.base] if resolved.base else []) + resolved.post):
            fn = getattr(c.runtime(), method)
            try:
                results.append(fn(*args, **kwargs))
            except Exception as exc:
                if resolved.on_error == OnError.DENY or c.kind == "security":
                    log.error("能力 %s/%s 增强执行失败, fail-closed 拒行: %s",
                              name, method, exc)
                    raise
                log.warning("能力 %s/%s 增强失败, 降级跳过: %s", name, method, exc)
                continue
        return results

    def guarded(self, name: str, method: str, default: Any = None) -> Callable[..., Any]:
        """返回一个"保护性调用"包装器: 跑 pre→base→post, 失败按 on_error 策略兜底。

        - security 能力: 任何增强失败 → 抛错 (调用方据此 fail-closed 拒绝)
        - 能力增强: 单个增强失败 → 跳过继续, 都不行 → 返回 default
        """
        resolved = self.resolve(name)
        on_error = resolved.on_error

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            members = resolved.pre + ([resolved.base] if resolved.base else []) + resolved.post
            for c in members:
                try:
                    return getattr(c.runtime(), method)(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if on_error == OnError.DENY or c.kind == "security":
                        raise
                    log.warning("保护性调用 %s/%s 失败, 继续下一个: %s",
                                name, method, exc)
            if on_error == OnError.DENY or any(c.kind == "security" for c in members):
                raise RuntimeError(f"能力 {name}/{method} 无可用的增强实现") from last_exc
            return default

        return wrapper

    # ---------------------------------------------------------- discovery

    def _autoload(self) -> None:
        """惰性加载第三方扩展 (entry-points + QXT_EXT_PATH)。只执行一次。"""
        if self._auto_loaded:
            return
        with self._load_lock:
            if self._auto_loaded:
                return
            self._load_entry_points()
            self._load_ext_path()
            self._auto_loaded = True

    def _load_entry_points(self) -> None:
        try:
            eps = importlib.metadata.entry_points()
            if hasattr(eps, "select"):
                group = eps.select(group=_ENTRY_POINT_GROUP)
            else:  # 旧版 importlib.metadata
                group = eps.get(_ENTRY_POINT_GROUP, [])
            for ep in group:
                try:
                    loader = ep.load()
                    loader(self)  # 约定: 扩展加载器接收注册表用于 register()
                    log.debug("已加载扩展 entry-point: %s", ep.name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("扩展 entry-point %s 加载失败: %s", ep.name, exc)
        except Exception as exc:  # noqa: BLE001
            log.debug("无可用 entry-point 组 %s: %s", _ENTRY_POINT_GROUP, exc)

    def _load_ext_path(self) -> None:
        raw = os.environ.get("QXT_EXT_PATH", "")
        if not raw:
            return
        for p in raw.split(os.pathsep):
            if not p:
                continue
            self._load_from_dir(p)

    def _load_from_dir(self, directory: str) -> None:
        ext_dir = Path(directory)
        if not ext_dir.is_dir():
            return
        import importlib.util

        for py in sorted(ext_dir.glob("*.py")):
            if py.stem.startswith("_") or py.stem == "setup":
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"qxt_ext_{py.stem}", py
                )
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                register = getattr(mod, "register", None)
                if callable(register):
                    spec.loader.exec_module(mod)
                    register(self)  # 约定: 模块级 register(registry) 函数
                    log.debug("已加载扩展目录脚本: %s", py)
            except Exception as exc:  # noqa: BLE001
                log.warning("扩展脚本 %s 加载失败: %s", py, exc)


def _sort(contribs: List[Contributor]) -> None:
    """按 layer (builtin 优先) + priority 排序。"""
    contribs.sort(key=lambda c: (c.layer == Layer.THIRD_PARTY, c.priority, c.name))


# 全局默认注册表
_default = ExtensionRegistry()


def get_registry() -> ExtensionRegistry:
    return _default


def register(
    name: str,
    impl: Any = None,
    *,
    layer: str = "third_party",
    placement: str = "auto",
    priority: int = 100,
    kind: str = "capability",
    owner: str = "",
    factory: Optional[Callable[[], Any]] = None,
) -> None:
    """向全局注册表注册能力的便捷函数。"""
    get_registry().register(
        name,
        impl,
        layer=layer,
        placement=placement,
        priority=priority,
        kind=kind,
        owner=owner,
        factory=factory,
    )