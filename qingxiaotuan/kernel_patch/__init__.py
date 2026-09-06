"""内核补丁层 (Kernel Patch Layer) —— 扩展增强模块层。

在不对现有模块做任何修改的前提下, 为内核/既有实现注入声明式、可回滚、
可审计的增强补丁。与现有微内核、ServiceContainer、MiddlewareEventBus、
extension_registry 完全兼容 (详见 `docs/KERNEL_PATCH_LAYER.md`)。

典型用法::

    from qingxiaotuan.kernel_patch import install, get_manager
    mgr = install(kernel)            # 应用全部已注册补丁
    mgr.status()                     # 已应用 / 跳过 / 目标清单
    mgr.revert("_i18n_t_memo")       # 回滚单个补丁
    mgr.revert_all()                 # 全部回滚, 完全还原现场
"""

from __future__ import annotations

from .patch_base import (
    PatchSpec,
    SKIP_ORIGINAL,
    KernelPatchManager,
    get_manager,
    install,
    patch_impl,
)

__all__ = [
    "PatchSpec",
    "SKIP_ORIGINAL",
    "KernelPatchManager",
    "get_manager",
    "install",
    "patch_impl",
    "ensure_builtin_loaded",
    "has_builtin",
]


def ensure_builtin_loaded() -> None:
    """确保内置补丁已注册进全局管理器 (import 副作用注册)。"""
    from . import builtin  # noqa: F401


def has_builtin() -> bool:
    from .patch_base import _default_registry
    names = {s.name for s in _default_registry()._specs.values()}
    return bool(names)