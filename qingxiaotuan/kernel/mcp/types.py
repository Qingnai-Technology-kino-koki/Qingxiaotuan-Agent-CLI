"""MCP 桥接层类型。

复用 ``qingxiaotuan.kernel.contract`` 的 ``ContentPart`` 作为多模态内容块载体。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from ..contract import ContentPart

# ===================================================================== MCP 数据块


@dataclass
class MCPContentBlock:
    """单个 MCP 工具结果内容块（对应 ``MCPContentBlock``）。"""

    type: str
    text: Optional[str] = None
    data: Optional[str] = None
    mimeType: Optional[str] = None
    uri: Optional[str] = None
    resource: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MCPContentBlock":
        if not isinstance(d, dict):
            return cls(type="text", text=str(d))
        return cls(
            type=d.get("type", "unknown"),
            text=d.get("text"),
            data=d.get("data"),
            mimeType=d.get("mimeType"),
            uri=d.get("uri"),
            resource=d.get("resource"),
        )


@dataclass
class MCPToolResult:
    """一次 MCP 工具调用结果（对应 ``MCPToolResult``）。"""

    content: List[MCPContentBlock]
    isError: bool = False
    structuredContent: Optional[Any] = None
    meta: Optional[Dict[str, Any]] = None  # 对应 _meta


@dataclass
class MCPToolDefinition:
    """MCP server 暴露的一个工具声明（对应 ``MCPToolDefinition``）。"""

    name: str
    description: str = ""
    inputSchema: Dict[str, Any] = field(default_factory=dict)


# ===================================================================== MCPClient 协议


@runtime_checkable
class MCPClient(Protocol):
    """与某个 MCP server 的连接契约（对应 ``MCPClient`` 接口）。

    list_tools / call_tool / ping 均为协程；signal 为可选取消信号
    （支持 ``asyncio.Event`` 或任意带 ``is_set()`` 的对象）。
    """

    async def list_tools(self) -> List[MCPToolDefinition]:
        ...

    async def call_tool(
        self,
        name: str,
        args: Dict[str, Any],
        signal: Any = None,
    ) -> MCPToolResult:
        ...

    async def ping(self) -> None:
        ...

    def on_unexpected_close(self, listener: Callable[["UnexpectedCloseReason"], None]) -> None:
        ...


UnexpectedCloseReason = Dict[str, Any]  # {"error"?: Exception, "stderr"?: str}


# ===================================================================== ExecutableTool（kernel 侧）


@dataclass
class ExecutableToolContext:
    """工具执行上下文（对应 ``ExecutableToolContext`` 的最小子集）。"""

    signal: Any = None
    on_update: Optional[Callable[[Dict[str, Any]], None]] = None
    turn_id: int = 0
    tool_call_id: str = ""


ExecutableToolOutput = Any  # str | List[ContentPart]


@dataclass
class ExecutableToolResult:
    """工具执行结果（对应 ``ExecutableToolSuccessResult | ExecutableToolErrorResult``）。"""

    output: ExecutableToolOutput
    is_error: bool = False
    truncated: Optional[bool] = None
    note: Optional[str] = None


@dataclass
class ExecutableTool:
    """一个可执行的 MCP 工具（对应 ``ExecutableTool``）。

    ``resolve_execution(args)`` 返回一个执行计划：``{"approval_rule": ..., "execute": coro}``。
    """

    name: str
    description: str
    parameters: Dict[str, Any]
    resolve_execution: Callable[[Any], Any]

    def resolveExecution(self, args: Any) -> Any:  # 兼容 camelCase 调用方
        return self.resolve_execution(args)


@runtime_checkable
class McpServerConfigLike(Protocol):
    """任意 MCP server 配置（pydantic 模型或 dict 均可）。"""

    transport: str
