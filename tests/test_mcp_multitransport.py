"""MCP 多传输融合层测试 —— 不依赖任何外部网络/MCP server。

覆盖:
- 远程 server 配置识别 (is_remote_server)
- kernel MCPToolResult 文本化 (format_mcp_tool_result)
- 碰撞安全命名 (>64 截断)
- MultiTransportMCP 注册 + 调用全链路（用假 manager/client 在真实事件循环线程上验证 loop 路由）
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from qingxiaotuan.kernel.mcp.naming import qualify_mcp_tool_name
from qingxiaotuan.kernel.mcp.types import MCPContentBlock, MCPToolResult
from qingxiaotuan.tools.mcp.multitransport import (
    MultiTransportMCP,
    format_mcp_tool_result,
    is_remote_server,
)
from qingxiaotuan.tools.mcp.plugin import register_mcp_tool
from qingxiaotuan.tools.base import Tool


# --------------------------------------------------------------------- 配置识别

def test_is_remote_classification():
    assert is_remote_server({"url": "https://x/mcp"})
    assert is_remote_server({"transport": "http", "url": "https://x"})
    assert is_remote_server({"transport": "sse", "url": "https://x"})
    assert not is_remote_server({"command": "npx", "args": []})
    assert not is_remote_server({"transport": "stdio", "command": "npx"})
    assert not is_remote_server({"name": "no-transport"})
    assert not is_remote_server("not-a-dict")


# --------------------------------------------------------------------- 结果格式化

def test_format_mcp_tool_result_text():
    res = MCPToolResult(content=[MCPContentBlock(type="text", text="hello")], isError=False)
    assert format_mcp_tool_result(res) == "hello"


def test_format_mcp_tool_result_error_prefix():
    res = MCPToolResult(content=[MCPContentBlock(type="text", text="boom")], isError=True)
    assert format_mcp_tool_result(res) == "[MCP 工具错误] boom"


def test_format_mcp_tool_result_multi_block():
    res = MCPToolResult(content=[
        MCPContentBlock(type="text", text="a"),
        MCPContentBlock(type="text", text="b"),
    ], isError=False)
    assert format_mcp_tool_result(res) == "a\nb"


# --------------------------------------------------------------------- 命名

def test_safe_name_short():
    assert qualify_mcp_tool_name("fs", "read") == "mcp__fs__read"


def test_safe_name_truncation():
    server = "very_long_server_name_" * 3
    tool = "very_long_tool_name_" * 3
    name = qualify_mcp_tool_name(server, tool)
    assert len(name) <= 64
    assert name.startswith("mcp__")
    assert "_" in name  # 含 8 位 hash 分隔


# --------------------------------------------------------------------- 注册 + 调用全链路

class _FakeEntry:
    def __init__(self, name, status, transport="http"):
        self.name = name
        self.status = status
        self.transport = transport
        self.tool_count = 0
        self.error = None


class _FakeClient:
    def __init__(self, result: MCPToolResult):
        self._result = result
        self.calls = []

    async def call_tool(self, name, args, signal=None):
        self.calls.append((name, args))
        return self._result


class _FakeManager:
    def __init__(self, entries, resolved):
        self._entries = entries
        self._resolved = resolved

    def list(self):
        return self._entries

    def resolved(self, name):
        return self._resolved.get(name)


class _Registry:
    def __init__(self):
        self.tools = {}

    def register(self, tool: Tool):
        self.tools[tool.name] = tool


def _make_mt(fake_manager):
    """构造一个已就绪的 MultiTransportMCP，但不走真实 connect_all（替换 manager + 事件循环线程）。"""
    mt = MultiTransportMCP(timeout=5.0)
    mt._manager = fake_manager
    mt._loop = asyncio.new_event_loop()
    mt._thread = threading.Thread(target=mt._loop.run_forever, daemon=True)
    mt._thread.start()
    return mt


def _fake_result():
    return MCPToolResult(
        content=[MCPContentBlock(type="text", text="pong")], isError=False
    )


def test_multitransport_register_and_call():
    client = _FakeClient(_fake_result())
    entry = _FakeEntry("api", "connected", "http")
    resolved = {
        "api": {
            "client": client,
            "raw_tools": [
                type("TD", (), {"name": "echo", "description": "echo tool",
                                "inputSchema": {"type": "object", "properties": {}}})(),
            ],
            "enabled_names": {"echo"},
        }
    }
    mt = _make_mt(_FakeManager([entry], resolved))
    try:
        registry = _Registry()
        mt.register_tools(registry, {"api": None}, register_mcp_tool)
        # 工具应以碰撞安全命名注册
        expected = qualify_mcp_tool_name("api", "echo")
        assert expected in registry.tools, registry.tools.keys()
        # 调用工具 -> 经 loop 路由到假 client -> 文本化结果
        out = registry.tools[expected].handler(None, text="hi")
        assert out == "pong"
        assert client.calls == [("echo", {"text": "hi"})]
    finally:
        mt.shutdown()


def test_multitransport_status_and_disabled_server():
    entry_ok = _FakeEntry("ok", "connected", "http")
    entry_fail = _FakeEntry("bad", "failed", "http")
    entry_fail.error = "boom"
    mt = _make_mt(_FakeManager([entry_ok, entry_fail], {}))
    try:
        statuses = {e.name: e.status for e in mt.status()}
        assert statuses == {"ok": "connected", "bad": "failed"}
        # failed server 不会被注册任何工具
        registry = _Registry()
        mt.register_tools(registry, {}, register_mcp_tool)
        assert registry.tools == {}
    finally:
        mt.shutdown()


def test_multitransport_disabled_tools_filtered():
    client = _FakeClient(_fake_result())
    entry = _FakeEntry("api", "connected", "http")
    resolved = {
        "api": {
            "client": client,
            "raw_tools": [
                type("TD", (), {"name": "keep", "description": "k",
                                "inputSchema": {}})(),
                type("TD", (), {"name": "drop", "description": "d",
                                "inputSchema": {}})(),
            ],
            "enabled_names": {"keep"},  # drop 被 disabledTools 过滤
        }
    }
    mt = _make_mt(_FakeManager([entry], resolved))
    try:
        registry = _Registry()
        mt.register_tools(registry, {"api": None}, register_mcp_tool)
        names = set(registry.tools.keys())
        assert qualify_mcp_tool_name("api", "keep") in names
        assert qualify_mcp_tool_name("api", "drop") not in names
    finally:
        mt.shutdown()
