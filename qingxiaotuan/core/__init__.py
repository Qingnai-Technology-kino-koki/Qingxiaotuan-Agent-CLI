"""核心内核与运行循环。"""

from __future__ import annotations

import importlib

__all__ = [
    "Kernel", "Plugin", "PluginError", "plugin",
    "ServiceContainer", "MiddlewareEventBus", "Middleware",
    "Agent", "DevLoop",
    "Reflector", "ReflectDecision", "ReflectResult",
    "build_system_prompt",
    "LoopProvider", "ReActLoop", "PlannerExecuteLoop", "DevLoopProvider", "LoopRegistry",
]

# 惰性导出: agent/devloop/reflector/prompts 依赖 tools 全链 (~300ms)。
# 轻量命令 (如 cron status 只用到 core.background_store) 无需付该代价;
# `from qingxiaotuan.core import X` 与 `core.X` 均照常工作 (PEP 562)。
_CORE_SOURCES = {
    "Kernel": (".kernel", "Kernel"),
    "Plugin": (".kernel", "Plugin"),
    "PluginError": (".kernel", "PluginError"),
    "plugin": (".kernel", "plugin"),
    "ServiceContainer": (".kernel", "ServiceContainer"),
    "MiddlewareEventBus": (".kernel", "MiddlewareEventBus"),
    "Middleware": (".kernel", "Middleware"),
    "Agent": (".agent", "Agent"),
    "DevLoop": (".devloop", "DevLoop"),
    "Reflector": (".reflector", "Reflector"),
    "ReflectDecision": (".reflector", "ReflectDecision"),
    "ReflectResult": (".reflector", "ReflectResult"),
    "build_system_prompt": (".prompts", "build_system_prompt"),
    # 可插拔 Loop
    "LoopProvider": (".loop_provider", "LoopProvider"),
    "ReActLoop": (".loop_provider", "ReActLoop"),
    "PlannerExecuteLoop": (".loop_provider", "PlannerExecuteLoop"),
    "DevLoopProvider": (".loop_provider", "DevLoopProvider"),
    "LoopRegistry": (".loop_provider", "LoopRegistry"),
}


def __getattr__(name: str):
    mapping = _CORE_SOURCES.get(name)
    if mapping is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod = importlib.import_module(mapping[0], __package__)
    return getattr(mod, mapping[1])
