"""内核补丁层 —— 核心机制 (扩展增强模块层的引擎)。

定位
----
一个与现有微内核 **完全兼容** 的"内核补丁类"增强层。它**不修改** core/ kernel/
ext/ arch/ 任何文件的实现, 而是在运行时对指定"现有可调用对象"执行声明式
增强包装, 支持:

  - 有序    : priority 决定应用顺序, 同目标可叠加
  - 版本锚定: min_kernel 高于当前版本则跳过 (版本感知)
  - 可逆    : 保存原引用, revert 完全还原现场
  - 审计    : apply/revert/bypass/error 均经内核事件总线广播
  - 懒解析  : 声明时零成本, apply 时才 import 目标模块并包装
  - fail-open: 默认补丁异常不影响主功能 (仅记录); 安全类可声明 fail-closed

为什么兼容现有架构
------------------
  - 通过 `@plugin` 注册进 Kernel, 经 `kernel.activate_all()` 激活;
  - 以 "kernel_patch" 服务暴露在 ServiceContainer, 其它插件可 require;
  - 经 MiddlewareEventBus 发 "patch.*" 事件, 供审计/观测中间件消费;
  - 与 ext/extension_registry 的"能力贡献者叠加"**正交**:
      前者 = 对外挂新能力; 本层 = 对现有内部实现的安全热补丁。
"""

from __future__ import annotations

import importlib
import inspect
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# 《不并存的哨兵》: before 模式返回它表示"跳过原实现, 直接采用本补丁返回值"
SKIP_ORIGINAL = object()


# ===================================================================== 版本工具

def _version_tuple(v: str) -> Tuple[int, ...]:
    out: List[int] = []
    for part in str(v).replace("v", "").split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _satisfies_min(current: str, minimum: str) -> bool:
    """current 是否满足 minimum 版本要求。minimum 为空视为总是满足。"""
    if not minimum or minimum == "0.0.0":
        return True
    return _version_tuple(current) >= _version_tuple(minimum)


# ===================================================================== 补丁声明

@dataclass
class PatchSpec:
    """一个内核补丁的声明。由 @patch_impl 装饰器产生并注册。"""
    name: str
    target: str                 # 点路径: "Module.fn" 或 "Module.Class.method"
    impl: Callable              # 补丁实现 (按 mode 决定签名)
    priority: int = 100         # 越小越先应用 (同目标叠加顺序)
    version: str = "0.1.0"      # 本补丁版本
    min_kernel: str = "0.0.0"   # 要求的内核最低版本; 低于则跳过
    mode: str = "wrap"          # wrap / before / after / replace
    kind: str = "capability"    # capability: fail-open; security: fail-closed
    enabled: bool = True


def patch_impl(
    target: str,
    *,
    priority: int = 100,
    version: str = "0.1.0",
    min_kernel: str = "0.0.0",
    mode: str = "wrap",
    kind: str = "capability",
):
    """声明式补丁装饰器: 把实现函数注册为对 target 的新增补丁。

    mode 语义:
      - wrap   : impl(original, *args, **kw) -> 完全控制 (默认, 推荐)
      - replace: impl(*args, **kw) -> 整体覆盖 (原实现被保存但不调用)
      - before : impl(*args, **kw) -> 若返回 SKIP_ORIGINAL 则采用其返回,
                  否则落到原实现
      - after  : impl(result, *args, **kw) -> 原实现先执行, 再处理结果

    用法::

        @patch_impl("qingxiaotuan.i18n.t", priority=10, min_kernel="0.2.0")
        def _t_memo(original, key, **kw):
            ...  # 命中缓存直接返回, 否则 original(key, **kw)
    """
    def deco(fn: Callable) -> Callable:
        spec = PatchSpec(
            name=getattr(fn, "__name__", "patch"),
            target=target,
            impl=fn,
            priority=int(priority),
            version=str(version),
            min_kernel=str(min_kernel),
            mode=mode,
            kind=kind,
        )
        _default_registry().register(spec)
        return fn
    return deco


# ===================================================================== 目标解析

class TargetPatcher:
    """负责把补丁挂到目标属性上并存还原。线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # target -> (owner_module, attr_path, previous_value)
        self._saved: Dict[str, Tuple[Any, str, Any]] = {}

    def current_value(self, target: str) -> Any:
        host, attr = self._resolve_host(target)
        return getattr(host, attr)

    def _resolve_host(self, target: str) -> Tuple[Any, str]:
        """target 点路径 -> 末尾「宿主对象」与末段属性名。

        取「最长可导入模块前缀」作为宿主, 剩余段作属性遍历:
          - "pkg.mod.fn"          -> host=模块, attr=fn
          - "pkg.mod.Class.meth"  -> host=Class(经模块遍历), attr=meth
        """
        parts = target.strip(".").split(".")
        if len(parts) < 2:
            raise ValueError(f"补丁目标需为点路径 (Module.attr), got: {target}")
        host: Any = None
        # 模块前缀最多取到 parts[:-1] (末段是我们要置换的属性名)
        for i in range(len(parts) - 1, 0, -1):
            modpath = ".".join(parts[:i])
            try:
                host = importlib.import_module(modpath)
            except ImportError:
                continue
            for seg in parts[i:-1]:        # 中间段做属性遍历, 不含末段
                host = getattr(host, seg)
            return host, parts[-1]
        raise ImportError(f"补丁目标无法解析为已安装模块: {target}")

    def apply(self, spec: PatchSpec) -> bool:
        """把补丁包装到目标上; 若目标已被本 patcher 打过补丁, 返回 False (排队)。
        """
        with self._lock:
            if spec.target in self._saved:
                return False  # 已有补丁在; 多补丁叠加由 caller 保证 priority 序
            host, attr = self._resolve_host(spec.target)
            original = getattr(host, attr)
            if not callable(original):
                log.warning("补丁 %s: 目标 %s 不可调用, 跳过", spec.name, spec.target)
                return False
            wrapper = self._make_wrapper(spec, original)
            setattr(host, attr, wrapper)
            self._saved[spec.target] = (host, attr, original)
            return True

    def revert(self, target: str) -> bool:
        with self._lock:
            reco = self._saved.pop(target, None)
            if reco is None:
                return False
            host, attr, original = reco
            setattr(host, attr, original)
            return True

    def revert_all(self) -> int:
        with self._lock:
            targets = list(self._saved.keys())
        n = 0
        for t in reversed(targets):
            if self.revert(t):
                n += 1
        return n

    def applied_targets(self) -> List[str]:
        with self._lock:
            return list(self._saved.keys())

    @staticmethod
    def _make_wrapper(spec: PatchSpec, original: Callable) -> Callable:
        impl = spec.impl
        kind = spec.kind
        mode = spec.mode

        if mode == "replace":
            @_wraps(original)
            def _repl(*a: Any, **kw: Any) -> Any:
                return impl(*a, **kw)
            return _repl

        if mode == "before":
            # 语义: impl 先跑; 返回 SKIP_ORIGINAL 表示"交给原实现", 否则直接采用返回值。
            @_wraps(original)
            def _bef(*a: Any, **kw: Any) -> Any:
                r = impl(*a, **kw)
                if r is SKIP_ORIGINAL:
                    return original(*a, **kw)
                return r
            return _bef

        if mode == "after":
            @_wraps(original)
            def _aft(*a: Any, **kw: Any) -> Any:
                res = original(*a, **kw)
                return impl(res, *a, **kw)
            return _aft

        # wrap (默认)
        @_wraps(original)
        def _wrap(*a: Any, **kw: Any) -> Any:
            return impl(original, *a, **kw)
        return _wrap


def _wraps(original: Callable) -> Callable:
    """fs 级保守拷贝: 仅复制能安全复制的属性。"""
    def deco(wrapper: Callable) -> Callable:
        try:
            import functools
            return functools.wraps(original)(wrapper)
        except Exception:  # noqa: BLE001
            wrapper.__name__ = getattr(original, "__name__", wrapper.__name__)
            return wrapper
    return deco


# ===================================================================== 管理器

class KernelPatchManager:
    """集合内核补丁: 注册 + 应用 + 回滚 + 审计。

    兼容 Kernel: 以 "kernel_patch" 服务注册, 内部持有 kernel 引用来 emit 事件。
    """

    def __init__(self, kernel: Any = None) -> None:
        self.kernel = kernel
        self._specs: Dict[str, PatchSpec] = {}
        self._order: List[str] = []
        self._patcher = TargetPatcher()
        self._applied: List[str] = []       # 应用顺序 (LIFO 回滚)
        self._skipped: Dict[str, str] = {}  # name -> 跳过原因
        self._lock = threading.RLock()

    # ---------------------------------------------------------- 注册 / 发现

    def register(self, spec: PatchSpec) -> None:
        with self._lock:
            if spec.name in self._specs:
                log.debug("补丁 %s 重复注册, 覆盖", spec.name)
            self._specs[spec.name] = spec
            if spec.name not in self._order:
                self._order.append(spec.name)
        self._order.sort(key=lambda n: (self._specs[n].priority, n))

    def discover(self, module: Any = None) -> "KernelPatchManager":
        """从模块的 patch_impl 注册继承到本管理器 (幂等, 可叠加多次 cwd)。"""
        from . import _default_registry
        src = _default_registry()
        with self._lock:
            for name, spec in src._specs.items():
                if name not in self._specs:
                    self.register(spec)
        return self

    def specs(self) -> List[PatchSpec]:
        with self._lock:
            return [self._specs[n] for n in self._order]

    # ---------------------------------------------------------- 版本检查

    def _check_version(self, spec: PatchSpec) -> Optional[str]:
        if not spec.enabled:
            return "disabled"
        from .. import __version__
        if not _satisfies_min(__version__, spec.min_kernel):
            return f"min_kernel={spec.min_kernel!r} > 当前 {__version__}"
        return None

    # ---------------------------------------------------------- 应用 / 回滚

    def apply_all(self) -> Tuple[int, int]:
        """应用全部已注册补丁, 返回 (已应用, 跳过)。"""
        applied_n = 0
        skipped_n = 0
        for spec in self.specs():
            ok, reason = self.apply(spec.name)
            if ok:
                applied_n += 1
            else:
                skipped_n += 1
                self._skipped[spec.name] = reason
        return applied_n, skipped_n

    def apply(self, name: str) -> Tuple[bool, str]:
        spec = self._specs.get(name)
        if spec is None:
            return False, "unknown"
        reason = self._check_version(spec)
        if reason:
            self._emit("bypass", name, reason=reason)
            return False, reason
        try:
            placed = self._patcher.apply(spec)
        except Exception as exc:  # noqa: BLE001
            self._emit("error", name, error=str(exc))
            if spec.kind == "security":
                raise
            return False, f"apply 异常: {exc}"
        if not placed:
            reason = "目标已被其它补丁占用 (不支持同点多次叠加)"
            self._emit("bypass", name, reason=reason)
            return False, reason
        with self._lock:
            if name not in self._applied:
                self._applied.append(name)
        self._emit("applied", name)
        log.debug("内核补丁已应用: %s -> %s", name, spec.target)
        return True, "ok"

    def revert_all(self) -> int:
        with self._lock:
            names = list(self._applied)
        n = 0
        for name in reversed(names):
            if self.revert(name):
                n += 1
        return n

    def revert(self, name: str) -> bool:
        spec = self._specs.get(name)
        if spec is None:
            return False
        try:
            restored = self._patcher.revert(spec.target)
        except Exception as exc:  # noqa: BLE001
            self._emit("error", name, error=f"revert 异常: {exc}")
            return False
        if restored:
            with self._lock:
                if name in self._applied:
                    self._applied.remove(name)
            self._emit("reverted", name)
            log.debug("内核补丁已回滚: %s", name)
        return restored

    # ---------------------------------------------------------- 状态

    def status(self) -> Dict[str, Any]:
        return {
            "kernel_version": _current_version(),
            "registered": [s.name for s in self.specs()],
            "applied": list(self._applied),
            "skipped": dict(self._skipped),
            "patched_targets": self._patcher.applied_targets(),
        }

    # ---------------------------------------------------------- 审计

    def _emit(self, event: str, name: str, **extra: Any) -> None:
        if self.kernel is None:
            return
        try:
            self.kernel.emit(f"patch.{event}", {"name": name, **extra})
        except Exception:  # noqa: BLE001
            pass


def _current_version() -> str:
    try:
        from .. import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "0.0.0"


# ===================================================================== 全局注册

_registry_lock = threading.Lock()
_registry: Optional[KernelPatchManager] = None


def _default_registry() -> KernelPatchManager:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = KernelPatchManager()
        return _registry


def get_manager(kernel: Any = None) -> KernelPatchManager:
    """获取全局补丁管理器; 有 kernel 时绑定事件审计。"""
    mgr = _default_registry()
    if kernel is not None:
        mgr.kernel = kernel
    return mgr


def install(kernel: Any = None) -> KernelPatchManager:
    """一键安装: 发现所有已注册补丁并应用。返回管理器。"""
    mgr = _default_registry()
    if kernel is not None:
        mgr.kernel = kernel
    try:
        from . import builtin  # noqa: F401  确保内置补丁已注册 (import 副作用)
    except Exception:  # noqa: BLE001
        log.warning("内置补丁注册失败: 继续安装其它补丁")
    mgr.apply_all()
    return mgr