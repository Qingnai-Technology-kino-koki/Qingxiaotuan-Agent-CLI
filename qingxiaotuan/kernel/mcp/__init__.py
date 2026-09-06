"""kernel MCP 桥接子系统 —— 自研实现 —— MCP (Model Context Protocol) 的客户端与工具桥接。

零新依赖：stdio 用 ``asyncio.subprocess`` 自实现 JSON-RPC 行帧；http/sse 用 httpx。
对齐锚点：复用 ``qingxiaotuan.kernel.contract``（ContentPart / Tool 等）与
``qingxiaotuan.tools.base``（Tool / ToolRegistry）做桥接。

核心导出：
- ``McpConnectionManager``：管理多个 MCP server 连接生命周期与状态广播。
- ``qualify_mcp_tool_name`` / ``is_mcp_tool_name``：工具名限定与判定。
- ``create_mcp_tool``：把 MCP 工具包装为 kernel ``ExecutableTool``。
- ``MCPClient``：client 契约协议。
- ``load_mcp_config``：解析 ``{mcpServers:{...}}`` 配置。
"""

from __future__ import annotations

from .config import (
    McpServerConfig,
    McpServerHttpConfig,
    McpServerSseConfig,
    McpServerStdioConfig,
    compute_enabled_names,
    load_mcp_config,
    merge_mcp_configs,
    resolve_config,
)
from .connection_manager import McpConnectionManager
from .registry_integration import McpToolCollision, McpToolRegistry, register_mcp_server
from .bridge import bridge_to_existing_mcp, get_all_mcp_tools
from .errors import (
    McpConnectionError,
    McpError,
    McpStartupError,
    McpTimeoutError,
    McpToolNameCollision,
    McpTransportError,
)
from .naming import (
    is_mcp_tool_name,
    qualify_mcp_tool_name,
    sanitize_mcp_name_part,
)
from .tool import create_mcp_tool
from .types import (
    ExecutableTool,
    ExecutableToolContext,
    ExecutableToolResult,
    MCPClient,
    MCPContentBlock,
    MCPToolDefinition,
    MCPToolResult,
)

__all__ = [
    "McpConnectionManager",
    "McpToolRegistry",
    "McpToolCollision",
    "register_mcp_server",
    "bridge_to_existing_mcp",
    "get_all_mcp_tools",
    "qualify_mcp_tool_name",
    "is_mcp_tool_name",
    "sanitize_mcp_name_part",
    "create_mcp_tool",
    "MCPClient",
    "load_mcp_config",
    "resolve_config",
    "merge_mcp_configs",
    "compute_enabled_names",
    "McpServerConfig",
    "McpServerStdioConfig",
    "McpServerHttpConfig",
    "McpServerSseConfig",
    "MCPToolDefinition",
    "MCPToolResult",
    "MCPContentBlock",
    "ExecutableTool",
    "ExecutableToolContext",
    "ExecutableToolResult",
    "McpError",
    "McpStartupError",
    "McpConnectionError",
    "McpTransportError",
    "McpTimeoutError",
    "McpToolNameCollision",
]
