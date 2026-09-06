"""MCP Tool Search 上下文降耗测试 (对标 Claude Code MCP Tool Search)。"""

from __future__ import annotations

import json

import pytest

from qingxiaotuan.tools.base import Tool, ToolRegistry
from qingxiaotuan.tools.mcp_tool_search import (
    ContextBudget,
    DEFAULT_THRESHOLD,
    MCPToolSearchEngine,
    estimate_tokens,
    _tokenize,
)


def _mk_mcp(name: str, desc: str, props: dict) -> Tool:
    return Tool(
        name=f"mcp__server__{name}",
        description=desc,
        parameters={"type": "object", "properties": props,
                    "required": list(props)[:1]},
        handler=lambda _ctx, **k: "ok",
        group="mcp",
    )


def _big_desc(n: int) -> str:
    return "capability keyword " * n + " detailed schema description".upper()


# ---------------------------------------------------------------- ContextBudget

def test_budget_threshold_default():
    b = ContextBudget(context_window=100_000)
    assert b.threshold == pytest.approx(DEFAULT_THRESHOLD)
    assert b.should_search(9_999) is False
    assert b.should_search(10_000) is True  # 10% 触发


def test_budget_utilization():
    b = ContextBudget(context_window=200_000)
    assert b.utilization(20_000) == pytest.approx(0.10)


def test_estimate_tokens_monotonic():
    assert estimate_tokens("x" * 40) >= 10
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)


# ---------------------------------------------------------------- Engine 检索

def _engine_big_mcp() -> MCPToolSearchEngine:
    eng = MCPToolSearchEngine(budget=ContextBudget(context_window=200_000))
    eng.add(_mk_mcp("read_file", "Read a file from disk (r/w)", {"path": {}}))
    eng.add(_mk_mcp("send_message", "Send a chat message", {"text": {}}))
    return eng


def test_engine_indexes_mcp_only():
    eng = MCPToolSearchEngine()
    eng.add(_mk_mcp("read_file", "read file", {"path": {}}))
    eng.add(Tool(name="run_shell", description="run", parameters={},
                 handler=lambda *a, **k: "x", group="general"))
    assert eng.tool_count == 1  # 非 MCP 不纳入


def test_search_returns_relevant_schema():
    eng = _engine_big_mcp()
    hits = eng.search("read file from disk")
    assert hits, "应命中 read_file 工具"
    fn = hits[0]["function"]
    assert "mcp__server__read_file" in fn["name"]
    # schema 是完整声明
    assert fn["parameters"]["properties"]["path"] == {}


def test_search_no_match_ok():
    eng = _engine_big_mcp()
    assert eng.search("zzz none none") == []
    assert eng.search("") == []


def test_search_call_count_tracks():
    eng = _engine_big_mcp()
    eng.search("read")
    eng.search("message")
    assert eng.search_call_count == 2


# ---------------------------------------------------------------- 第二轮: 驻留 + 打分

def test_name_match_scores_higher():
    eng = MCPToolSearchEngine()
    eng.add(_mk_mcp("send_message", "communicate via chat api", {"text": {}}))
    eng.add(_mk_mcp("read_file", "communicate via chat api", {"path": {}}))
    hits = eng.search("message")
    assert hits[0]["function"]["name"] == "mcp__server__send_message"


def test_search_sticky_pins_top_hits():
    eng = MCPToolSearchEngine()
    eng.add(_mk_mcp("read_file", "read file", {"path": {}}))
    eng.add(_mk_mcp("send_message", "send chat", {"text": {}}))
    eng.search("read file", sticky=True)
    assert "mcp__server__read_file" in eng._sticky


def test_sticky_capped_by_max():
    eng = MCPToolSearchEngine(max_sticky=1)
    eng.add(_mk_mcp("a", "read file", {"p": {}}))
    eng.add(_mk_mcp("b", "send chat", {"p": {}}))
    eng.add(_mk_mcp("c", "list dir", {"p": {}}))
    eng.search("read", sticky=True)
    eng.search("send", sticky=True)
    assert len(eng._sticky) == 1


def test_pin_and_unpin():
    eng = MCPToolSearchEngine()
    eng.add(_mk_mcp("read_file", "read", {"p": {}}))
    eng.pin(["mcp__server__read_file", "unknown"])
    assert len(eng._sticky) == 1
    eng.unpin("mcp__server__read_file")
    assert eng._sticky == []


def test_reduce_keeps_sticky_full_schema():
    eng = MCPToolSearchEngine(budget=ContextBudget(context_window=2000))
    big = _big_desc(60)
    eng.add(_mk_mcp("read_file", big + " read file", {"path": {}}))
    eng.add(_mk_mcp("zip_tool", big, {"d": {}}))
    eng.pin(["mcp__server__read_file"])
    tools = [
        _mk_mcp("read_file", big + " read file", {"path": {}}),
        _mk_mcp("zip_tool", big, {"d": {}}),
    ]
    out = eng.reduce(tools)
    names = [t.name for t in out]
    assert "mcp__tool_search" in names
    # 驻留工具保完整 schema, 非驻留 MCP 工具降耗
    assert "mcp__server__read_file" in names
    assert "mcp__server__zip_tool" not in names


# ---------------------------------------------------------------- 上下文降耗

def test_reduce_below_threshold_returns_all():
    eng = MCPToolSearchEngine(budget=ContextBudget(context_window=10_000_000))
    eng.add(_mk_mcp("read_file", "read", {"path": {}}))
    tools = [_mk_mcp("read_file", "read", {"path": {}}),
             Tool(name="run_shell", description="x", parameters={},
                  handler=lambda *a, **k: "y", group="shell")]
    out = eng.reduce(tools)
    names = [t.name for t in out]
    assert "mcp__server__read_file" in names
    assert "run_shell" in names
    # 未降耗: 不注入搜索 stub
    assert "mcp__tool_search" not in names


def test_reduce_over_threshold_compacts():
    eng = MCPToolSearchEngine(budget=ContextBudget(context_window=2000))
    eng.add(_mk_mcp("fat_tool", _big_desc(60), {"data": {}}))
    tools = [
        _mk_mcp("fat_tool", _big_desc(60), {"data": {}}),
        Tool(name="read_file", description="builtin", parameters={},
             handler=lambda *a, **k: "x", group="general"),
    ]
    out = eng.reduce(tools)
    names = [t.name for t in out]
    # 完整 MCP schema 被摘除, 替换为搜索 stub; 内置工具保留
    assert "mcp__server__fat_tool" not in names
    assert "mcp__tool_search" in names
    assert "read_file" in names
    # 降耗后的 schema 应远小于原 MCP schema (上下文降耗生效)
    compact_tokens = estimate_tokens([t.schema() for t in out])
    full_tokens = estimate_tokens(tools[0].schema())
    assert compact_tokens < full_tokens


def test_reduce_stub_dispatch_returns_search():
    eng = MCPToolSearchEngine(budget=ContextBudget(context_window=1500))
    eng.add(_mk_mcp("read_file", _big_desc(40) + " read file from disk",
                    {"path": {}}))
    tools = [_mk_mcp("read_file", _big_desc(40) + " read file from disk",
                     {"path": {}})]
    reduced = eng.reduce(tools)
    stub = next(t for t in reduced if t.name == "mcp__tool_search")
    text = stub.handler(None, search="read file")
    assert "read_file" in text
    assert "read file from disk" in text


def test_reduce_force_enabled_even_small():
    eng = MCPToolSearchEngine(
        budget=ContextBudget(context_window=10_000_000), enabled=True)
    eng.add(_mk_mcp("read_file", "read", {"path": {}}))
    out = eng.reduce([_mk_mcp("read_file", "read", {"path": {}})])
    assert any(t.name == "mcp__tool_search" for t in out)


def test_reduce_disabled_keeps_all():
    eng = MCPToolSearchEngine(
        budget=ContextBudget(context_window=10), enabled=False)
    eng.add(_mk_mcp("read_file", "read", {"path": {}}))
    out = eng.reduce([_mk_mcp("read_file", "read", {"path": {}})])
    assert "mcp__server__read_file" in [t.name for t in out]
    assert "mcp__tool_search" not in [t.name for t in out]


# ---------------------------------------------------------------- Schema 集成

def test_registry_schemas_applies_engine():
    reg = ToolRegistry()
    reg.register(Tool(name="read_file", description="builtin", parameters={},
                      handler=lambda *a, **k: "x", group="general"))
    reg.register(_mk_mcp("fat", _big_desc(80), {"d": {}}))
    eng = MCPToolSearchEngine(budget=ContextBudget(context_window=1500)).install(reg)
    eng.add(reg.get("mcp__server__fat"))
    reg.tool_search_engine = eng

    schemas = reg.schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "mcp__tool_search" in names
    assert "mcp__server__fat" not in names
    assert "read_file" in names


# ---------------------------------------------------------------- tokenize

def test_tokenize_filters_stopwords():
    toks = _tokenize("Read the file and list contents")
    assert "read" in toks
    assert "file" in toks
    assert "the" not in toks


# ---------------------------------------------------------------- 第三轮: CLI 与边界

class _FakeUI:
    def __init__(self):
        self.lines = []
    def info(self, s): self.lines.append(s)
    def warn(self, s): self.lines.append(s)
    def success(self, s): self.lines.append(s)


def test_slash_mcp_tools_report(monkeypatch):
    eng = MCPToolSearchEngine(budget=ContextBudget(context_window=100_000))
    eng.add(_mk_mcp("read_file", _big_desc(80), {"path": {}}))
    class FakeAgent:
        kernel = type("K", (), {"get": lambda s, n: eng if n == "mcp_tool_search" else None})()
    from qingxiaotuan.cli import cmd_slash
    fake = _FakeUI()
    monkeypatch.setattr(cmd_slash, "ui", fake)
    cmd_slash._cmd_mcp_tools(FakeAgent(), "")
    joined = "\n".join(fake.lines)
    assert "MCP Tool Search" in joined
    assert "已接入 MCP 工具" in joined


def test_slash_mcp_tools_toggle(monkeypatch):
    eng = MCPToolSearchEngine()
    class FakeAgent:
        kernel = type("K", (), {"get": lambda s, n: eng if n == "mcp_tool_search" else None})()
    from qingxiaotuan.cli import cmd_slash
    fake = _FakeUI()
    monkeypatch.setattr(cmd_slash, "ui", fake)
    cmd_slash._cmd_mcp_tools(FakeAgent(), "off")
    assert eng.enabled is False
    cmd_slash._cmd_mcp_tools(FakeAgent(), "on")
    assert eng.enabled is True


def test_slash_mcp_tools_search(monkeypatch):
    eng = MCPToolSearchEngine()
    eng.add(_mk_mcp("read_file", "read file from disk", {"path": {}}))
    class FakeAgent:
        kernel = type("K", (), {"get": lambda s, n: eng if n == "mcp_tool_search" else None})()
    from qingxiaotuan.cli import cmd_slash
    fake = _FakeUI()
    monkeypatch.setattr(cmd_slash, "ui", fake)
    cmd_slash._cmd_mcp_tools(FakeAgent(), "search read file")
    assert any("read_file" in l for l in fake.lines)


def test_slash_dispatch_routes_mcp_tools(monkeypatch):
    eng = MCPToolSearchEngine()
    class FakeAgent:
        kernel = type("K", (), {"get": lambda s, n: eng if n == "mcp_tool_search" else None})()
        registry = type("R", (), {"tools": []})()
    from qingxiaotuan.cli import cmd_slash
    fake = _FakeUI()
    monkeypatch.setattr(cmd_slash, "ui", fake)
    assert cmd_slash._handle_slash("/mcp-tools", FakeAgent(), None, "") is True


def test_context_window_floor():
    b = ContextBudget(context_window=50)
    assert b.context_window >= 1000
    assert b.utilization(10) < 1.0


def test_reduce_threshold_clamped():
    b = ContextBudget(threshold=1.5)
    assert b.threshold == 1.0
    b2 = ContextBudget(threshold=-0.5)
    assert b2.threshold == 0.0


def test_engine_no_mcp_no_stub():
    reg = ToolRegistry()
    reg.register(Tool(name="read_file", description="b", parameters={},
                      handler=lambda *a, **k: "x", group="general"))
    eng = MCPToolSearchEngine(enabled=True).install(reg)
    reg.tool_search_engine = eng
    names = [s["function"]["name"] for s in reg.schemas()]
    assert "read_file" in names
    assert "mcp__tool_search" not in names