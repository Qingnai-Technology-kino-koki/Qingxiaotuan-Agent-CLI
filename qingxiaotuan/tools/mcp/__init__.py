"""MCP 支持包。"""

from .client import MCPClient, MCPError
from .plugin import MCPPlugin

__all__ = ["MCPClient", "MCPError", "MCPPlugin"]
