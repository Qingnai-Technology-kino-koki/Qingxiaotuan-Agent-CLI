"""MCP 工具注册集成 —— 把 MCP 工具并入工具注册表并处理命名碰撞。

- ``McpToolRegistry``：把连接状态变化映射为「注册/反注册」kernel 可执行 MCP 工具。
- 同名碰撞检测 ``McpToolCollision``：输家丢弃（同 server 内同名 / 跨 server 限定名冲突）。
- ``register_mcp_server``：便捷函数，把某 server 的工具批量注册进本地 registry。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .naming import qualify_mcp_tool_name
from .tool import create_mcp_tool
from .types import ExecutableTool, MCPClient, MCPToolDefinition


@dataclass
class McpToolCollision:
    """一个工具名碰撞（输家应被丢弃）。"""

    qualified: str
    tool_name: str
    collides_with: Dict[str, Any]  # {"kind":"same_server","tool_name":...} | {"kind":"other_server","server_name":...}


@dataclass
class RegisterResult:
    registered: List[str]
    collisions: List[McpToolCollision] = field(default_factory=list)


class McpToolRegistry:
    """本地 MCP 工具注册表（存储 ``ExecutableTool``，可选同步桥接到 ``tools.base.ToolRegistry``）。"""

    def __init__(self, registry: Any = None) -> None:
        # registry: 可选外部 ToolRegistry（qingxiaotuan.tools.base.ToolRegistry），
        # 提供时把 MCP 工具桥接为 BaseTool 一并注册进去。
        self._tools: Dict[str, ExecutableTool] = {}
        self._by_server: Dict[str, List[str]] = {}
        self._server_of: Dict[str, str] = {}
        self._registry = registry

    # ----------------------------------------------------------- 注册/反注册

    def register_server(
        self,
        server_name: str,
        client: MCPClient,
        raw_tools: List[MCPToolDefinition],
        enabled_names: Optional[set] = None,
    ) -> RegisterResult:
        self.unregister_server(server_name)
        registered: List[str] = []
        collisions: List[McpToolCollision] = []
        seen_this: Dict[str, str] = {}

        for tool in raw_tools:
            if enabled_names is not None and tool.name not in enabled_names:
                continue
            qualified = qualify_mcp_tool_name(server_name, tool.name)

            # 同 server 内限定名碰撞
            if qualified in seen_this:
                collisions.append(
                    McpToolCollision(
                        qualified,
                        tool.name,
                        {"kind": "same_server", "tool_name": seen_this[qualified]},
                    )
                )
                continue
            # 跨 server 限定名碰撞（已注册的同名）
            if qualified in self._tools:
                collisions.append(
                    McpToolCollision(
                        qualified,
                        tool.name,
                        {"kind": "other_server", "server_name": self._server_of.get(qualified, "")},
                    )
                )
                continue

            seen_this[qualified] = tool.name
            exe = create_mcp_tool(qualified, tool, client)
            self._tools[qualified] = exe
            self._server_of[qualified] = server_name
            if self._registry is not None:
                self._registry.register(_bridge_executable_to_base_tool(exe))
            registered.append(qualified)

        self._by_server[server_name] = registered
        return RegisterResult(registered=registered, collisions=collisions)

    def unregister_server(self, server_name: str) -> bool:
        names = self._by_server.get(server_name)
        if names is None:
            return False
        for name in names:
            self._tools.pop(name, None)
            self._server_of.pop(name, None)
            if self._registry is not None:
                self._registry._tools.pop(name, None)
        self._by_server.pop(server_name, None)
        return True

    def get(self, name: str) -> Optional[ExecutableTool]:
        return self._tools.get(name)

    @property
    def tools(self) -> List[ExecutableTool]:
        return list(self._tools.values())

    # ----------------------------------------------------------- 状态驱动

    def attach_to(self, manager: Any) -> Callable[[], None]:
        """订阅连接管理器的状态变化，自动注册/反注册工具。"""
        return manager.on_status_change(lambda entry: self._on_status(entry, manager))

    def _on_status(self, entry: Any, manager: Any) -> None:
        if entry.status == "connected":
            resolved = manager.resolved(entry.name)
            if resolved is not None:
                self.register_server(
                    entry.name,
                    resolved["client"],
                    resolved["raw_tools"],
                    resolved["enabled_names"],
                )
        elif entry.status in ("disabled", "failed", "removed", "needs-auth"):
            self.unregister_server(entry.name)


async def register_mcp_server(
    server_name: str,
    client: MCPClient,
    raw_tools: List[MCPToolDefinition],
    registry: Any,
    enabled_names: Optional[set] = None,
) -> List[ExecutableTool]:
    """便捷函数：把某 server（已连接的 client + 工具清单）注册进 registry。

    ``registry`` 可为 ``McpToolRegistry`` 或外部 ``tools.base.ToolRegistry``
    （若为后者，内部用 ``McpToolRegistry`` 包装并桥接）。返回注册成功的 ``ExecutableTool`` 列表。
    """
    if isinstance(registry, McpToolRegistry):
        reg = registry
    else:
        reg = McpToolRegistry(registry)
    result = reg.register_server(server_name, client, raw_tools, enabled_names)
    return [reg.get(n) for n in result.registered]


def _bridge_executable_to_base_tool(exe: ExecutableTool):  # 延迟导入，避免加载 tools.base
    from .bridge import bridge_executable_to_base_tool

    return bridge_executable_to_base_tool(exe)
