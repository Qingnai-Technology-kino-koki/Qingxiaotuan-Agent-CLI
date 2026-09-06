"""工具系统入口: 注册表服务插件 + 全部内置工具插件。"""

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, ToolRegistry
from .cache import ToolResultCache
from .checkpoint import CheckpointPlugin
from .filesystem import FilesystemPlugin
from .memory_tool import MemoryToolPlugin
from .shell import ShellPlugin
from .skill_tool import SkillToolPlugin
from .web import WebPlugin
from .code import CodeToolPlugin
from .dispatch import DispatchPlugin
from .task_tool import TaskToolPlugin
from .session_tools import SessionToolsPlugin
from .mcp.plugin import MCPPlugin
from .pipeline import PipelinePlugin
from .code_review import CodeReviewPlugin
from .external import ExternalToolsPlugin
from .languages import LanguagePlugin
from .permissions import PermissionPolicy
from .permission_fusion import FusionPermissionPolicy, build_permission_policy
from .code_graph import CodeGraphPlugin
from .backend_dev import BackendDevPlugin
from .sandbox import SandboxPlugin
from .dynamic_workflow_tool import DynamicWorkflowPlugin

__all__ = [
    "Tool", "ToolContext", "ToolRegistry",
    "PermissionPolicy", "FusionPermissionPolicy", "build_permission_policy",
    "ToolRegistryPlugin", "builtin_tool_plugins",
]


class ToolRegistryPlugin(Plugin):
    """最底层的工具注册表服务, 其他工具插件都依赖它。"""

    name = "tools.registry"
    provides = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        # 只读工具结果缓存 (TTL 60s, 上限 200 条), 减少多轮循环里的重复 IO/摘要
        cfg = kernel.get("config")
        ttl = cfg.get("tools.cache_ttl", 60.0) if cfg is not None else 60.0
        cache = ToolResultCache(ttl=ttl)
        kernel.provide("tool_cache", cache, owner=self.name)
        kernel.provide("tool_registry", ToolRegistry(cache=cache), owner=self.name)


def builtin_tool_plugins():
    return [
        FilesystemPlugin(), ShellPlugin(), WebPlugin(),
        MemoryToolPlugin(), SkillToolPlugin(), CodeToolPlugin(),
        DispatchPlugin(), PipelinePlugin(), CodeReviewPlugin(),
        ExternalToolsPlugin(), LanguagePlugin(), CheckpointPlugin(),
        TaskToolPlugin(),
        SessionToolsPlugin(), CodeGraphPlugin(), BackendDevPlugin(), SandboxPlugin(),
        DynamicWorkflowPlugin(),
    ]

