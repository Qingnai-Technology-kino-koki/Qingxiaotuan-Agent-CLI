"""Loop 插件 —— 向 Kernel 注册可插拔 Loop 架构。

默认装载 ReActLoop; 用户可通过以下方式切换:
  1. config: loop.provider = "plan_execute" | "devloop" | "react"
  2. 代码: kernel.provide("loop_provider", MyLoop())
  3. 运行时: /loop <name> 切换
"""

from __future__ import annotations

from typing import Any

from .kernel import Kernel, Plugin, plugin
from .loop_provider import (
    LoopProvider,
    LoopRegistry,
    ReActLoop,
    PlannerExecuteLoop,
    DevLoopProvider,
)


@plugin(
    "loop.provider",
    version="0.1.0",
    provides=["loop_registry", "loop_provider"],
    requires=["config"],
)
class LoopPlugin(Plugin):
    """Loop 插件: 注册 LoopRegistry 并按配置设置默认 Loop。"""

    def activate(self, kernel: Kernel) -> None:
        config = kernel.require("config")

        registry = LoopRegistry()

        # 注册内置 Loop
        registry.register(ReActLoop())
        registry.register(PlannerExecuteLoop())
        registry.register(DevLoopProvider())

        # 按配置设置默认 Loop
        default_loop = config.get("loop.provider", "react")
        try:
            registry.set_current(default_loop)
        except ValueError:
            pass  # 配置值无效时保持默认 ReAct

        kernel.provide("loop_registry", registry, owner=self.name)
        # 提供当前 Loop 的快捷引用 (Agent 可直接读取)
        kernel.provide("loop_provider", registry.get_current(), owner=self.name)

    def switch_loop(self, kernel: Kernel, name: str) -> None:
        """运行时切换 Loop (供 /loop 命令调用)。"""
        registry: LoopRegistry = kernel.require("loop_registry")
        registry.set_current(name)
        # 更新快捷引用
        kernel.services.unprovide("loop_provider")
        kernel.provide("loop_provider", registry.get_current(), owner=self.name)
        kernel.emit("loop.switched", {"name": name})
