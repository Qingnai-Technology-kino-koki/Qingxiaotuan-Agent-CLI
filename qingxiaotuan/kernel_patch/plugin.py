"""内核补丁层的装配 Plugin —— 无缝接入现有微内核。

作为 `@plugin("kernel_patch")` 注册进 Kernel, 被 `kernel.activate_all()` 激活。
激活时:
  1. ensure_builtin_loaded() 归并内置补丁到全局管理器;
  2. 绑定 kernel (供 patch.* 事件广播);
  3. 以 "kernel_patch" 服务 provide 给 Kernel, 其它插件/命令可 require;
  4. 立即 apply_all() 应用全部已注册补丁 (含版本感知跳过)。

deactivate 时 revert_all(), 完全还原补丁现场 —— 与内核生命周期共生, 不残留。
"""

from __future__ import annotations

from typing import Any

from ..core.kernel import Kernel, Plugin, plugin
from . import ensure_builtin_loaded, patch_base


@plugin(
    "kernel_patch",
    version="1.0.0",
    provides=["kernel_patch"],
    requires=[],
)
class KernelPatchPlugin(Plugin):
    name = "kernel_patch"
    version = "1.0.0"
    provides = ["kernel_patch"]
    requires: list = []

    def activate(self, kernel: Kernel) -> None:
        ensure_builtin_loaded()          # import 副作用注册内置补丁
        mgr = patch_base.get_manager(kernel)   # 绑定 kernel, 复用全局单例
        kernel.provide("kernel_patch", mgr, owner=self.name)
        applied, skipped = mgr.apply_all()
        kernel.emit("patch.activated", {
            "name": self.name,
            "applied": applied,
            "skipped": skipped,
        })

    def deactivate(self, kernel: Kernel) -> None:
        try:
            mgr = patch_base.get_manager()
            reverted = mgr.revert_all()
            kernel.emit("patch.deactivated", {"name": self.name, "reverted": reverted})
        except Exception:  # noqa: BLE001
            pass