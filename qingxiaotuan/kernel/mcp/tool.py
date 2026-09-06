"""MCP 工具包装 —— 把远端 MCP 工具包成青小团可执行工具。

把 MCP server 暴露的一个工具包装为 kernel ``ExecutableTool``：
- ``resolve_execution(args)`` 返回 ``{"approval_rule": qualified_name, "execute": coro}``。
- ``execute`` 调用 ``client.call_tool(tool.name, args, signal)``，失败按传输失败重试一次
  （经 ``options.reconnect`` 拿到新 client 后重发）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .errors import (
    is_mcp_connection_closed_error,
    is_mcp_transport_failure,
)
from .output import result_to_output
from .types import ExecutableTool, ExecutableToolContext, MCPClient, MCPToolDefinition


def create_mcp_tool(
    qualified_name: str,
    tool_def: MCPToolDefinition,
    client: MCPClient,
    options: Optional[Dict[str, Any]] = None,
) -> ExecutableTool:
    """创建一个 kernel 可执行 MCP 工具。"""
    options = options or {}
    tool_name = tool_def.name

    def resolve_execution(args: Any) -> Dict[str, Any]:
        async def execute(ctx: ExecutableToolContext) -> Dict[str, Any]:
            if options.get("is_removed") and options["is_removed"]() is True:
                return {
                    "output": (
                        f'MCP server for tool "{qualified_name}" has been removed '
                        "(plugin uninstalled or config deleted). Do not call this tool again."
                    ),
                    "is_error": True,
                }
            signal = getattr(ctx, "signal", None)
            try:
                result = await client.call_tool(tool_name, args or {}, signal)
            except Exception as error:  # 断线 -> 重试
                result = await _retry_after_reconnect(
                    error, client, tool_name, args, ctx, options
                )
            return result_to_output(result, qualified_name)

        return {"approval_rule": qualified_name, "execute": execute, "is_error": False}

    return ExecutableTool(
        name=qualified_name,
        description=tool_def.description,
        parameters=tool_def.inputSchema,
        resolve_execution=resolve_execution,
    )


async def _retry_after_reconnect(
    error: Exception,
    client: MCPClient,
    tool_name: str,
    args: Any,
    ctx: ExecutableToolContext,
    options: Dict[str, Any],
) -> Any:
    reconnect = options.get("reconnect")
    signal = getattr(ctx, "signal", None)

    aborted = signal is not None and hasattr(signal, "is_set") and signal.is_set()
    if reconnect is None or aborted or not is_mcp_transport_failure(error):
        raise error

    # 连接未彻底断开（非 ConnectionClosed）：先探活，活着就直接重试原 client
    if not is_mcp_connection_closed_error(error):
        try:
            await client.ping()
            return await client.call_tool(tool_name, args or {}, signal)
        except Exception:
            pass  # 探活失败，进入重连

    try:
        fresh_client = await reconnect(signal)
    except Exception as reconnect_error:
        if signal is not None and hasattr(signal, "is_set") and signal.is_set():
            raise reconnect_error
        raise type(error)(
            f"{error} (reconnecting the MCP server also failed: {reconnect_error})"
        ) from reconnect_error

    if fresh_client is None:
        raise error
    return await fresh_client.call_tool(tool_name, args or {}, signal)
