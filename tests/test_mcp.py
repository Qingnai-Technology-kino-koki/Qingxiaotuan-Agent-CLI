"""MCP 桥接测试: 客户端握手/调用、插件桥接注册、CLI 子命令。

不依赖任何外部网络/MCP server —— 用 tests/fixtures/mcp_demo_server.py
(纯标准库的最小 stdio MCP server) 做端到端验证。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_demo_server.py"
sys.path.insert(0, str(ROOT))

from qingxiaotuan.tools.mcp.client import MCPClient  # noqa: E402
from qingxiaotuan.tools.mcp.plugin import MCPPlugin  # noqa: E402
from qingxiaotuan.tools.base import Tool  # noqa: E402


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, dotted, default=None):
        node = self.data
        for k in dotted.split("."):
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set_user(self, dotted, value):
        node = self.data
        keys = dotted.split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value


class FakeRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool


class FakeKernel:
    def __init__(self):
        self._prov = {}

    def provide(self, name, obj, owner=None):
        self._prov[name] = obj

    def require(self, name):
        return self._prov[name]


def _make_client():
    return MCPClient(
        name="demo", command=sys.executable, args=[str(FIXTURE)], timeout=15.0
    )


@pytest.fixture
def client():
    c = _make_client()
    c.start()
    yield c
    try:
        c.stop()
    except Exception:
        pass


# ------------------------------------------------------------------ client

def test_client_handshake_and_list(client):
    tools = client.list_tools()
    names = {t["name"] for t in tools}
    assert {"echo_text", "add_numbers"} <= names


def test_client_call_echo(client):
    assert client.call_tool("echo_text", {"text": "hi"}) == "echo: hi"


def test_client_call_add(client):
    assert client.call_tool("add_numbers", {"a": 2, "b": 3}) == "5"


def test_client_call_unknown_tool_is_error(client):
    out = client.call_tool("nope", {})
    assert "错误" in out or "error" in out.lower()


def test_client_disabled_server_does_not_crash():
    # 命令不存在时应抛出异常 (由 plugin 捕获), 而不是死锁。
    bad = MCPClient(name="bad", command="this_command_does_not_exist_xyz", args=[], timeout=5.0)
    with pytest.raises(Exception):
        bad.start()


# ------------------------------------------------------------------ plugin

def test_plugin_bridges_remote_tools_as_local():
    cfg = FakeConfig({
        "mcp": {
            "enabled": True,
            "timeout": 15.0,
            "servers": [{"name": "demo", "command": sys.executable, "args": [str(FIXTURE)]}],
        }
    })
    reg = FakeRegistry()
    kernel = FakeKernel()
    kernel.provide("config", cfg)
    kernel.provide("tool_registry", reg)

    plugin = MCPPlugin()
    plugin.activate(kernel)
    try:
        assert "mcp__demo__echo_text" in reg.tools
        assert "mcp__demo__add_numbers" in reg.tools

        tool = reg.tools["mcp__demo__echo_text"]
        assert tool.group == "mcp"
        # 桥接后的 handler 应当把调用转发到远端 server
        assert tool.handler(None, text="bridge") == "echo: bridge"
    finally:
        plugin.deactivate(kernel)


def test_plugin_disabled_skips_activation():
    cfg = FakeConfig({"mcp": {"enabled": False, "servers": [
        {"name": "demo", "command": sys.executable, "args": [str(FIXTURE)]}
    ]}})
    reg = FakeRegistry()
    kernel = FakeKernel()
    kernel.provide("config", cfg)
    kernel.provide("tool_registry", reg)
    plugin = MCPPlugin()
    plugin.activate(kernel)
    # 禁用时不应注册任何工具, 也不应启动子进程
    assert reg.tools == {}


# ------------------------------------------------------------------ CLI 子命令

def _fake_kernel_with_client():
    c = _make_client()
    c.start()
    kernel = FakeKernel()
    kernel.provide("mcp_clients", [c])
    kernel.provide("config", FakeConfig({"mcp": {"servers": [
        {"name": "demo", "command": sys.executable, "args": [str(FIXTURE)]}
    ]}}))
    return kernel, c


def test_cmd_mcp_list(monkeypatch):
    import qingxiaotuan.cli.commands as C
    kernel, c = _fake_kernel_with_client()
    monkeypatch.setattr(C, "build_kernel", lambda *a, **k: kernel)
    try:
        ns = argparse.Namespace(mcp_cmd="list", server=None, tool=None, json=None,
                                name=None, command=None, args=[], env=[], timeout=None,
                                workspace=".")
        assert C.cmd_mcp(ns) == 0
    finally:
        c.stop()


def test_cmd_mcp_tools(monkeypatch):
    import qingxiaotuan.cli.commands as C
    kernel, c = _fake_kernel_with_client()
    monkeypatch.setattr(C, "build_kernel", lambda *a, **k: kernel)
    try:
        ns = argparse.Namespace(mcp_cmd="tools", server="demo", tool=None, json=None,
                                name=None, command=None, args=[], env=[], timeout=None,
                                workspace=".")
        assert C.cmd_mcp(ns) == 0
    finally:
        c.stop()


def test_cmd_mcp_call(monkeypatch):
    import qingxiaotuan.cli.commands as C
    kernel, c = _fake_kernel_with_client()
    monkeypatch.setattr(C, "build_kernel", lambda *a, **k: kernel)
    try:
        ns = argparse.Namespace(mcp_cmd="call", server="demo", tool="echo_text",
                                json='{"text":"cli"}', name=None, command=None,
                                args=[], env=[], timeout=None, workspace=".")
        assert C.cmd_mcp(ns) == 0
    finally:
        c.stop()


def test_cmd_mcp_add_persists(monkeypatch):
    import qingxiaotuan.cli.commands as C
    fake_cfg = FakeConfig({"mcp": {"servers": []}})
    monkeypatch.setattr(C, "Config", lambda *a, **k: fake_cfg)
    ns = argparse.Namespace(mcp_cmd="add", server=None, tool=None, json=None,
                            name="fs", command="npx", args=["-y", "server-fs"],
                            env=["TOKEN=x"], timeout=20.0, workspace=".")
    assert C.cmd_mcp(ns) == 0
    servers = fake_cfg.get("mcp.servers", [])
    assert len(servers) == 1
    assert servers[0]["name"] == "fs"
    assert servers[0]["command"] == "npx"
    assert servers[0]["args"] == ["-y", "server-fs"]
    assert servers[0]["env"] == {"TOKEN": "x"}
    assert servers[0]["timeout"] == 20.0
