"""测试: web_search 工具 (DuckDuckGo HTML 版) —— 对标 Claude Code 的 WebSearch。

覆盖:
- parse_ddg_results 纯函数: uddg 跳转链接还原 / 标签剥离 / 条数截断
- _decode_ddg_href: // 开头补 https: 协议
- web_search handler: 输出格式 / 空搜索词 / 条数夹取 / 网络异常兜底
- WebPlugin 注册: web_search 为只读工具
不访问真实网络 (_ddg_fetch 被 monkeypatch)。
"""

from __future__ import annotations

import json

import pytest

from qingxiaotuan.tools.base import ToolContext, ToolRegistry
from qingxiaotuan.tools.web import (
    WebPlugin,
    _decode_ddg_href,
    parse_ddg_results,
    web_search,
)

# 典型 DDG html.duckduckgo.com 结果页片段 (两条结果)
SAMPLE_HTML = """
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=abc">
       <b>Python</b> 官网</a>
  </h2>
  <a class="result__snippet" href="#">
    The official home of the <b>Python</b> Programming   Language.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F&amp;x=1">文档</a>
  <a class="result__snippet">Python 文档站</a>
</div>
"""


def _ctx() -> ToolContext:
    tc = ToolContext.__new__(ToolContext)
    tc.kernel = None
    tc.workspace = "."
    tc.config = lambda dotted, default=None: default  # type: ignore[method-assign]
    return tc


# ----------------------------------------------------------------- 纯函数解析

def test_decode_ddg_href_unwraps_uddg_and_protocol():
    assert _decode_ddg_href(
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=x"
    ) == "https://www.python.org/"
    # 非跳转链接原样返回并补协议
    assert _decode_ddg_href("//example.com/page") == "https://example.com/page"


def test_parse_ddg_results_extracts_title_url_snippet():
    rows = parse_ddg_results(SAMPLE_HTML, max_results=5)
    assert len(rows) == 2
    assert rows[0]["title"] == "Python 官网"          # <b> 标签被剥掉
    assert rows[0]["url"] == "https://www.python.org/"
    assert rows[0]["snippet"] == "The official home of the Python Programming Language."
    assert rows[1]["url"] == "https://docs.python.org/"


def test_parse_ddg_results_truncates_and_handles_empty():
    assert len(parse_ddg_results(SAMPLE_HTML, max_results=1)) == 1
    assert parse_ddg_results("<html></html>") == []


# ----------------------------------------------------------------- handler

def test_web_search_formats_results(monkeypatch):
    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", lambda url, timeout: SAMPLE_HTML)
    out = web_search(_ctx(), "python", max_results=5)
    assert out.startswith("1. Python 官网")
    assert "https://www.python.org/" in out
    assert "2. 文档" in out


def test_web_search_empty_query():
    assert "不能为空" in web_search(_ctx(), "   ")


def test_web_search_clamps_max_results(monkeypatch):
    seen: dict[str, object] = {}

    def fake_fetch(url, timeout):
        seen["url"] = url
        return SAMPLE_HTML

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    out = web_search(_ctx(), "python", max_results=99)  # 夹到 10, 不报错即可
    assert "1. Python 官网" in out
    assert "q=python" in str(seen["url"])


def test_web_search_network_failure_friendly(monkeypatch):
    def boom(url, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", boom)
    out = web_search(_ctx(), "python")
    assert out.startswith("[web_search] 搜索失败")
    assert "TimeoutError" in out


def test_web_search_no_results(monkeypatch):
    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", lambda url, timeout: "<html/>")
    assert '未找到与 "xyz" 相关的结果' in web_search(_ctx(), "xyz")


# ----------------------------------------------------------------- 注册与并行安全

def test_web_plugin_registers_search_tool():
    reg = ToolRegistry()

    class _Cfg:
        def get(self, dotted, default=None):
            return default

    class _Kernel:
        def get(self, name):
            if name == "config":
                return _Cfg()
            if name == "tool_registry":
                return reg
            return None

        def require(self, name):
            got = self.get(name)
            assert got is not None, name
            return got

    WebPlugin().activate(_Kernel())  # type: ignore[arg-type]
    tool = reg.get("web_search")
    assert tool is not None and tool.read_only
    schema = json.dumps(tool.schema(), ensure_ascii=False)
    assert '"query"' in schema and "web_search" in schema
