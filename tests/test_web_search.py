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
    _engine_bing,
    _engine_duckduckgo,
    parse_bing_results,
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
    tc.config = lambda dotted, default=None: (  # type: ignore[method-assign]
        False if dotted == "network.search_cache" else default)
    return tc


def _ctx_cfg(data: dict) -> ToolContext:
    """返回带 network 配置字典的上下文 (缺的键走默认值)。

    磁盘缓存默认关掉 (否则跨会话持久化会污染断言 fetch 次数的用例);
    需要测缓存的用例显式设 ``network.search_cache`` 与临时缓存目录。
    """
    base = dict(data)
    base.setdefault("network.search_cache", False)
    tc = ToolContext.__new__(ToolContext)
    tc.kernel = None
    tc.workspace = "."
    tc.config = lambda dotted, default=None: base.get(dotted, default)  # type: ignore[method-assign]
    return tc


def _sample_page(n: int, tag: str) -> str:
    """生成含 n 条结果、URL 带 tag 前缀的 DDG 结果页 HTML (便于去重/分页测试)。"""
    blocks = []
    for i in range(n):
        title = f"Result {tag}-{i} python docs"
        url = f"https://{tag}{i}.example.com/doc"
        blocks.append(
            f'<div class="result"><h2 class="result__title">'
            f'<a class="result__a" href="//duckduckgo.com/l/?uddg={url}">{title}</a></h2>'
            f'<a class="result__snippet">Snippet {tag}-{i} about python programming.</a></div>'
        )
    return "<html>" + "".join(blocks) + "</html>"


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


# ----------------------------------------------------------------- 分页 / 去重 / 截断 / top_k

def test_web_search_paginates_across_pages(monkeypatch):
    seen_starts: list[int] = []

    def fake_fetch(url, timeout):
        start = 0
        if "&start=" in url:
            start = int(url.split("&start=")[1])
        seen_starts.append(start)
        # 第 1 页 20 条 a*, 第 2 页 20 条 b* → 共 40 条
        return _sample_page(20, "a") if start == 0 else _sample_page(20, "b")

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    cfg = _ctx_cfg({"network.search_top_k": 0})  # 关掉 top_k, 验证分页不丢结果
    out = web_search(cfg, "python", max_results=40)
    assert "1. Result a-0" in out and "Result a-19" in out
    assert "Result b-0" in out and "Result b-19" in out
    assert seen_starts == [0, 20]  # 确实跨了 2 页


def test_web_search_dedups_identical_urls(monkeypatch):
    def fake_fetch(url, timeout):
        return _sample_page(20, "dup")  # 两页 URL 完全相同

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    out = web_search(_ctx(), "python", max_results=40)
    # 去重后只剩 20 条, 且不再抓第 3 页
    assert out.count("https://dup0.example.com/doc") == 1
    assert out.count("\n\n") <= 0  # 顺序输出, 无重复块


def test_web_search_truncates_long_snippets(monkeypatch):
    cfg = _ctx_cfg({"network.search_snippet_max_chars": 30})

    def make_page(url):
        # 把片段换成超长文本
        title = "LongTitle python"
        huge_snip = "x" * 200
        return (f'<div class="result"><h2 class="result__title">'
                f'<a class="result__a" href="//ddg/?uddg=https://long.example.com">{title}</a></h2>'
                f'<a class="result__snippet">{huge_snip}</a></div>')

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", lambda url, t: make_page(url))
    out = web_search(cfg, "python")
    # 摘要被截到 30 字符内并带省略号
    for line in out.splitlines():
        if "xxxx" in line:
            assert len(line.strip().rstrip("…")) <= 30
            assert line.rstrip().endswith("…")


def test_web_search_top_k_keeps_most_relevant(monkeypatch):
    # 两个结果: 一个标题含 query, 一个仅摘要含 → top_k=1 时只注入标题命中者
    html = (
        '<div class="result"><h2 class="result__title">'
        '<a class="result__a" href="//ddg/?uddg=https://hit.example.com">Clickbait python 标题直接命中</a></h2>'
        '<a class="result__snippet">nothing here</a></div>'
        '<div class="result"><h2 class="result__title">'
        '<a class="result__a" href="https://miss.example.com">ordinary page</a></h2>'
        '<a class="result__snippet">python 只出现在摘要里</a></div>'
    )
    cfg = _ctx_cfg({"network.search_top_k": 1})

    def fake_fetch(url, timeout):
        return html

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    out = web_search(cfg, "python")
    # 只返回 1 条, 且是最相关的标题命中者
    assert out.count("https://hit.example.com") == 1
    assert "miss.example.com" not in out


def test_web_search_caps_at_max_results(monkeypatch):
    # 配置上限 500: max_results 远超页数上限时自动分页抓取, 不无限抓
    cfg = _ctx_cfg({
        "network.search_max_results": 500,
        "network.search_max_pages": 3,
    })
    calls = []

    def fake_fetch(url, timeout):
        start = int(url.split("&start=")[1]) if "&start=" in url else 0
        calls.append(url)
        return _sample_page(20, "p" + str(start))  # 每页数据不同, 便于数分页次数

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    out = web_search(cfg, "python", max_results=500)
    assert len(calls) == 3  # 受 search_max_pages 约束, 不会无限分页
    assert out.count("https://p00.example.com/doc") == 1  # top_k 默认 5, 只注入最相关 5 条


# ----------------------------------------------------------------- 多引擎 fallback / Bing 解析

BING_HTML = """
<ol id="b_results"><li class="b_algo">
  <h2><a href="https://docs.bing.example.com/python">Bing Python 文档</a></h2>
  <p><b>official</b> python reference</p>
</li><li class="b_algo">
  <h2><a href="https://cn.bing.example.com/tutorial">python 教程</a></h2>
</li></ol>
"""


def _bing_page(n: int, tag: str) -> str:
    lis = []
    for i in range(n):
        lis.append(
            f'<li class="b_algo"><h2><a href="https://{tag}{i}.bing.example.com/doc">'
            f'{tag} {i} python</a></h2><p>snippet {i}</p></li>')
    return f"<ol id='b_results'>{''.join(lis)}</ol>"


def test_parse_bing_results_extracts_blocks():
    rows = parse_bing_results(BING_HTML, max_results=10)
    assert len(rows) == 2
    assert rows[0]["title"] == "Bing Python 文档"
    assert rows[0]["url"] == "https://docs.bing.example.com/python"
    assert rows[0]["snippet"] == "official python reference"  # <b> 剥离
    assert rows[1]["title"] == "python 教程"


def test_parse_bing_results_respects_cap_and_empty():
    assert len(parse_bing_results(BING_HTML, max_results=1)) == 1
    assert parse_bing_results("<html/>") == []


def test_web_search_falls_back_to_bing_when_ddg_fails(monkeypatch):
    """DDG 首页网络失败 → 自动切到 Bing 引擎并返回结果。"""
    calls = []

    def fake_fetch(url, timeout):
        calls.append(url)
        if "duckduckgo.com" in url:
            raise TimeoutError("ddg down")
        return _bing_page(3, "b")

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    out = web_search(_ctx(), "python")
    assert "bing.example.com" in out
    assert any("bing.com" in u for u in calls)  # 确实尝试了 Bing


def test_web_search_stays_on_ddg_when_it_succeeds(monkeypatch):
    """主引擎 DDG 成功时不用 fallback 到 Bing。"""
    calls = []

    def fake_fetch(url, timeout):
        calls.append(url)
        return SAMPLE_HTML

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    out = web_search(_ctx(), "python")
    assert "python.org" in out
    assert not any("bing.com" in u for u in calls)


def test_web_search_ddg_empty_then_bing_hits(monkeypatch):
    def fake_fetch(url, timeout):
        if "duckduckgo.com" in url:
            return "<html/>"          # DDG 无结果
        return _bing_page(2, "fb")    # Bing 有结果

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    out = web_search(_ctx(), "python")
    assert "fb0.bing.example.com" in out


# ----------------------------------------------------------------- 磁盘缓存

def test_web_search_disk_cache_shortcircuits_refetch(tmp_path, monkeypatch):
    """命中磁盘缓存后不再抓取网络 (重复查询省时省 token)。"""
    calls = []

    def fake_fetch(url, timeout):
        calls.append(url)
        return SAMPLE_HTML

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)

    def mk_cache_dir_ctx():
        return _ctx_cfg({
            "network.search_cache": True,
            "network.search_cache_dir": str(tmp_path),  # 隔离目录
        })

    first = web_search(mk_cache_dir_ctx(), "python", max_results=5)
    assert "python.org" in first
    assert calls  # 首查真实抓网并写缓存

    # 第二次同样的查询 → 直接命中缓存, 不再请求网络
    calls.clear()
    again = web_search(mk_cache_dir_ctx(), "python", max_results=5)
    assert "python.org" in again
    assert calls == []          # 0 次网络请求
    assert list(tmp_path.glob("*.json"))  # 缓存文件确实落盘


def test_web_search_cache_expired_refetches(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(url, timeout):
        calls.append(url)
        return SAMPLE_HTML

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)

    def ctx(ttl):
        return _ctx_cfg({
            "network.search_cache": True,
            "network.search_cache_dir": str(tmp_path),
            "network.search_cache_ttl": ttl,
        })

    web_search(ctx(2000), "python")
    assert calls
    # TTL 设为负 → 缓存必失效, 触发重新抓取
    calls.clear()
    web_search(ctx(-1), "python")
    assert calls  # 又重新请求了网络


def test_web_search_cache_disabled_always_network(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(url, timeout):
        calls.append(url)
        return SAMPLE_HTML

    monkeypatch.setattr("qingxiaotuan.tools.web._ddg_fetch", fake_fetch)
    ctx = _ctx_cfg({  # _ctx_cfg 默认 search_cache=False
        "network.search_cache_dir": str(tmp_path),
    })
    web_search(ctx, "python")
    calls.clear()
    web_search(ctx, "python")
    assert calls  # 关缓存时每次都联网


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
