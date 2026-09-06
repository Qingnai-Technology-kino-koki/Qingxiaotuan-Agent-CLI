"""MCP 桥接层错误模型。"""

from __future__ import annotations


class McpError(Exception):
    """MCP 桥接层错误基类。"""

    code = "mcp_error"


class McpStartupError(McpError):
    code = "mcp.startup_failed"


class McpConnectionError(McpError):
    """传输层连接断开（对应 ``isMcpConnectionClosedError``）。"""

    code = "mcp.connection_closed"


class McpTransportError(McpError):
    """广义传输失败（连接错误之外的底层 I/O 失败）。"""

    code = "mcp.transport_failure"


class McpTimeoutError(McpError):
    code = "mcp.timeout"


class McpToolNameCollision(Exception):
    """同名 MCP 工具碰撞：输家被丢弃，派发此错误记录碰撞信息。"""

    def __init__(self, message: str, collisions: "list") -> None:
        super().__init__(message)
        self.collisions = collisions


# --------------------------------------------------------------------------- 错误分类助手


def is_mcp_connection_closed_error(error: object) -> bool:
    return isinstance(error, McpConnectionError)


def is_mcp_transport_failure(error: object) -> bool:
    """传输失败 = 连接关闭错误 / 其它传输错误；非 MCP 结果错误。

    本实现中 JSON-RPC 业务错误也以 ``McpError`` 表达，但只有 ``Connection/Transport`` 类
    才视为可重连的传输失败。
    """
    if not isinstance(error, McpError):
        return False
    return isinstance(error, (McpConnectionError, McpTransportError))


def is_mcp_malformed_result_error(error: object) -> bool:
    return False
