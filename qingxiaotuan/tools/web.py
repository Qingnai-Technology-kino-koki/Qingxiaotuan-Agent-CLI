"""Web 工具插件: 抓取网页并转成可读文本。

健壮性要点 (工具层契约: 工具崩溃绝不能炸 Agent 循环):
- 所有网络/解码异常都被捕获, 返回友好错误字符串而非抛异常;
- 支持 gzip/deflate 响应体;
- 按 HTTP 响应头 charset 解码 (缺省 UTF-8, 错误字符替换), 对中文网页更友好;
- 超时 / URL 非法 / 非 http(s) 方案 都给出明确提示。
"""

from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Optional, Union

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop

MAX_PAGE = 15000
MAX_BODY = 2 * 1024 * 1024  # 响应体上限 2MB, 防止大页面拖垮 Agent
_TAG_RE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.IGNORECASE)
_HTML_RE = re.compile(r"<[^>]+>")
_META_CHARSET_RE = re.compile(r'<meta[^>]+charset=["\']?([^"\'\s>]+)', re.IGNORECASE)

# SSRF 防护: 内网/本地/保留地址段 (IPv4 + IPv6)。
_PRIVATE_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 解析不出合法 IP 视为可疑
    return any(ip in net for net in _PRIVATE_NETS)


_LOOPBACK_NETS = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]


def _check_ssrf(url: str, allow_loopback: bool = False) -> Optional[str]:
    """SSRF 防护: 拦截指向内网/本地/保留地址的 URL (含 DNS 解析结果)。

    allow_loopback=True 时放行 127.0.0.0/8 与 ::1 (本地开发服务器场景)。
    返回错误信息 (需拦截) 或 None (放行)。
    """
    def _blocked(ip: str) -> bool:
        if allow_loopback:
            try:
                ip_obj = ipaddress.ip_address(ip)
            except ValueError:
                pass
            else:
                if any(ip_obj in net for net in _LOOPBACK_NETS):
                    return False
        return _is_private_ip(ip)

    try:
        host = urllib.parse.urlparse(url).hostname
        if not host:
            return f"[web_fetch] 无法解析 URL 主机: {url!r}"
        # 字面 IP: 直接校验 (仅当 host 本身是 IP 字面量; 主机名交给下面的 DNS 解析校验)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if _blocked(host):
                return f"[web_fetch] 已拦截内网/本地地址: {url!r}"
        # 主机名: 解析全部 A/AAAA, 任一命中内网即拦截
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return f"[web_fetch] 无法解析域名: {host!r}"
        for info in infos:
            ip = str(info[4][0])
            if _blocked(ip):
                return f"[web_fetch] 已拦截解析到内网/本地地址的域名: {host!r} ({ip})"
    except Exception as exc:  # noqa: BLE001
        return f"[web_fetch] URL 校验失败: {type(exc).__name__}: {exc}"
    return None


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向时重新做 SSRF 校验, 并限制跳转次数 (默认 urllib 为 10 次)。"""

    max_redirections = 5
    allow_loopback = False

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        blocked = _check_ssrf(newurl, allow_loopback=self.allow_loopback)
        if blocked:
            raise urllib.error.URLError(blocked)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode_body(raw: bytes, content_type: str, text_head: str) -> str:
    """按响应头/ <meta charset> 解码, 失败回退 UTF-8 替换。"""
    charset = None
    if content_type:
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip('"').strip("'")
    if not charset:
        m = _META_CHARSET_RE.search(text_head[:2000])
        if m:
            charset = m.group(1)
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


# ----------------------------------------------------------------- 联网搜索 (DuckDuckGo HTML 版)

# DDG html.duckduckgo.com 结果页: 标题链接与摘要都带固定 class
_DDGA_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', re.IGNORECASE)
_DDGSNIPPET_RE = re.compile(
    r'<a[^>]*class="result__snippet"[^>]*>([\s\S]*?)</a>', re.IGNORECASE)


def _strip_tags(text: str) -> str:
    """去掉 HTML 标签并压平空白 (标题/摘要里的 <b> 高亮等)。"""
    return re.sub(r"\s+", " ", _HTML_RE.sub(" ", text)).strip()


def _decode_ddg_href(href: str) -> str:
    """还原 DDG 跳转链接里的真实目标 URL (uddg= 参数); // 开头补 https:。"""
    url = urllib.parse.unquote(href)
    m = re.search(r"uddg=([^&]+)", url)
    if m:
        url = urllib.parse.unquote(m.group(1))
    if url.startswith("//"):
        url = "https:" + url
    return url


def parse_ddg_results(html: str, max_results: int = 5) -> list[dict[str, str]]:
    """从 DDG HTML 搜索结果页提取 {title, url, snippet} 列表 (纯函数, 便于离线测试)。"""
    titles = [(_strip_tags(t), _decode_ddg_href(h))
              for h, t in _DDGA_RE.findall(html)]
    snippets = [_strip_tags(s) for s in _DDGSNIPPET_RE.findall(html)]
    out: list[dict[str, str]] = []
    for i, (title, url) in enumerate(titles[:max_results]):
        out.append({"title": title, "url": url,
                    "snippet": snippets[i] if i < len(snippets) else ""})
    return out


def _ddg_fetch(url: str, timeout: float) -> str:
    """抓取搜索结果页原始 HTML (含 gzip 解压), 异常交由调用方统一兜底。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; qingxiaotuan-agent)",
        "Accept-Encoding": "gzip",
        "Accept": "text/html",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw: bytes = resp.read(MAX_BODY)
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        charset: str = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def web_search(ctx: ToolContext, query: str, max_results: int = 5) -> str:
    """联网搜索: DuckDuckGo HTML 版, 返回 'N. 标题/URL/摘要' 列表。

    - 返回条数上限由 ``network.search_max_results`` 决定 (可配到 500);
    - 超过一页时自动分页抓取并按 URL 去重;
    - 每条摘要按 ``network.search_snippet_max_chars`` 截断;
    - 按 ``network.search_top_k`` 只把最相关的 top-k 条注入上下文 (token 最小化)。
    """
    if not isinstance(query, str) or not query.strip():
        return "[web_search] 搜索词不能为空"
    if max_results is None:
        max_results = ctx.config("network.search_default_results", 5)
    cap = int(ctx.config("network.search_max_results", 500))
    try:
        limit = max(1, min(int(max_results), max(1, cap)))
    except (TypeError, ValueError):
        limit = int(ctx.config("network.search_default_results", 5))
    timeout = float(ctx.config("network.fetch_timeout", ctx.config("tools.web.timeout", 30)))
    snippet_max = int(ctx.config("network.search_snippet_max_chars", 200))
    top_k = int(ctx.config("network.search_top_k", 5))

    results = _search_pages(ctx, query, limit, timeout, snippet_max)
    if isinstance(results, str):
        return results  # 所有搜索引擎首页都被网络失败拦截, 透出友好错误
    if not results:
        return f'[web_search] 未找到与 "{query}" 相关的结果'

    # 相关度排序 + top-k: 采得多、注入精, 把 token 消耗压到最小。
    if top_k > 0 and len(results) > top_k:
        results = sorted(results, key=lambda r: _relevance_score(query, r),
                         reverse=True)[:top_k]

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
    return "\n".join(lines)


# ----------------------------------------------------------------- 多引擎 + 磁盘缓存

# Bing HTML 结果: <li class="b_algo"> 块内含 <h2><a href> 标题 与 <p> 摘要
_BING_LI_RE = re.compile(r'<li[^>]*class="[^"]*b_algo[^"]*"[\s\S]*?</li>', re.IGNORECASE)
_BING_A_RE = re.compile(r'<h2[^>]*>[\s\S]*?<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', re.IGNORECASE)
_BING_P_RE = re.compile(r'<p[^>]*>([\s\S]*?)</p>', re.IGNORECASE)


def parse_bing_results(html: str, max_results: int = 10) -> list[dict[str, str]]:
    """从 Bing HTML 结果页提取 {title, url, snippet} (纯函数, 便于离线测试)。"""
    out: list[dict[str, str]] = []
    for li in _BING_LI_RE.findall(html):
        m = _BING_A_RE.search(li)
        ms = _BING_P_RE.search(li)
        snippet = _strip_tags(ms.group(1)) if ms else ""
        if m:
            href, title = m.group(1), _strip_tags(m.group(2))
            if not title or href.startswith(("javascript:", "/")):
                continue
            out.append({"title": title, "url": href, "snippet": snippet})
        elif snippet:  # 无标题但有摘要, 保底仍给出一条
            out.append({"title": "", "url": "", "snippet": snippet})
        if len(out) >= max_results:
            break
    return out


def _cache_dir(ctx) -> Optional[Path]:
    """解析磁盘缓存目录 (可配, 默认 ~/.qingxiaotuan/cache/web; 设为空字符串禁用)。"""
    cfg = ctx.config("network.search_cache_dir", "~/.qingxiaotuan/cache/web")
    if not cfg:
        return None
    try:
        return Path(os.path.expanduser(cfg))
    except Exception:  # noqa: BLE001
        return None


def _cache_key(query: str, limit: int, engine: str) -> str:
    raw = f"{engine}\0{query.strip().lower()}\0{limit}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_load(ctx, query: str, limit: int, engine: str, ttl: float,
                snippet_max: int) -> Optional[list[dict[str, str]]]:
    cd = _cache_dir(ctx)
    if cd is None or not ctx.config("network.search_cache", True):
        return None
    try:
        p = cd / f"{_cache_key(query, limit, engine)}.json"
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(data.get("ts", 0)) > ttl:
            return None
        results = data.get("results", [])
        for r in results:
            r["snippet"] = _truncate(r.get("snippet", ""), snippet_max)
        return results
    except Exception:  # noqa: BLE001 - 缓存损坏/不可读不影响主流程
        return None


def _cache_store(ctx, query: str, limit: int, engine: str,
                 results: list[dict[str, str]]) -> None:
    cd = _cache_dir(ctx)
    if cd is None or not results or not ctx.config("network.search_cache", True):
        return
    try:
        cd.mkdir(parents=True, exist_ok=True)
        p = cd / f"{_cache_key(query, limit, engine)}.json"
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(
            {"ts": time.time(), "query": query, "results": results},
            ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 - 缓存写入失败绝不影响搜索
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _collect_pages(ctx, query: str, engine: str, base: str, limit: int,
                   timeout: float, snippet_max: int, parse_fn, per_page: int,
                   page_param: str, step: int) -> "list[dict[str, str]] | str":
    """分页抓取单个搜索引擎的结果并按 URL 去重, 直到凑够 ``limit`` 条或触发页数上限。

    首页抓取失败返回错误字符串, 后续分页失败则用已收集的结果继续。
    """
    max_pages = int(ctx.config("network.search_max_pages", 25))
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    start = 0
    for _ in range(max_pages):
        if len(results) >= limit:
            break
        url = f"{base}&{page_param}={start}" if start else base
        try:
            html = _ddg_fetch(url, timeout)
        except Exception as exc:  # noqa: BLE001
            if not results:
                return f"[web_search] 搜索失败 ({engine}: {type(exc).__name__})"
            break
        new = 0
        for r in parse_fn(html, per_page):
            u = r.get("url", "")
            if not u or u in seen:
                continue
            seen.add(u)
            r["snippet"] = _truncate(r.get("snippet", ""), snippet_max)
            results.append(r)
            new += 1
        if new == 0:
            break  # 无新结果 (失效页/内容重复), 停止分页
        start += step
    return results[:limit]


def _engine_duckduckgo(ctx, query: str, limit: int, timeout: float,
                       snippet_max: int) -> "list[dict[str, str]] | str":
    ttl = float(ctx.config("network.search_cache_ttl", 21600))
    cached = _cache_load(ctx, query, limit, "duckduckgo", ttl, snippet_max)
    if cached is not None:
        return cached
    base = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    out = _collect_pages(ctx, query, "duckduckgo", base, limit, timeout,
                         snippet_max, parse_ddg_results, 20, "start", 20)
    if isinstance(out, list) and out:
        _cache_store(ctx, query, limit, "duckduckgo", out)
    return out


def _engine_bing(ctx, query: str, limit: int, timeout: float,
                 snippet_max: int) -> "list[dict[str, str]] | str":
    ttl = float(ctx.config("network.search_cache_ttl", 21600))
    cached = _cache_load(ctx, query, limit, "bing", ttl, snippet_max)
    if cached is not None:
        return cached
    base = ("https://www.bing.com/search?q=" + urllib.parse.quote_plus(query)
            + "&count=10")
    out = _collect_pages(ctx, query, "bing", base, limit, timeout,
                         snippet_max, parse_bing_results, 10, "first", 10)
    if isinstance(out, list) and out:
        _cache_store(ctx, query, limit, "bing", out)
    return out


_ENGINE_FNS = {
    "duckduckgo": _engine_duckduckgo,
    "bing": _engine_bing,
}
_DEFAULT_ENGINES = ["duckduckgo", "bing"]


def _search_pages(ctx, query: str, limit: int, timeout: float,
                  snippet_max: int) -> "list[dict[str, str]] | str":
    """按 ``network.search_engines`` 顺序尝试各引擎, 主引擎失败自动切换下一个。

    - 某引擎硬失败 (首页网络错误) 就换下一个;
    - 某引擎返回空结果也换下一个 (别的源可能有);
    - 全部硬失败 → 把首个错误透出; 全部空 → 返回 [] (由上层报「未找到」)。
    """
    engines = ctx.config("network.search_engines", _DEFAULT_ENGINES)
    if isinstance(engines, str):
        engines = [e.strip() for e in re.split(r"[,;\s]+", engines) if e.strip()]
        engines = engines or _DEFAULT_ENGINES
    hard_err = ""
    for eng in engines:
        fn = _ENGINE_FNS.get(eng)
        if not fn:
            continue
        out = fn(ctx, query, limit, timeout, snippet_max)
        if isinstance(out, str):      # 引擎级硬失败
            hard_err = hard_err or out
            continue
        if out:                       # 命中结果
            return out
        hard_err = ""                 # 该引擎返回空 → 重置首错, 尝试下一个
    return hard_err or []


def _truncate(text: str, max_chars: int) -> str:
    """把摘要/正文截到 ``max_chars`` 内, 控制进入上下文的 token 量。"""
    if not text or max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _relevance_score(query: str, r: dict[str, str]) -> int:
    """轻量相关度: 统计 query 分词在标题/URL/摘要里出现的次数 (越大越相关)。"""
    q_words = [w for w in re.split(r"\W+", query.lower()) if w]
    if not q_words:
        return 0
    haystack = " ".join([r.get("title", ""), r.get("url", ""),
                         r.get("snippet", "")]).lower()
    score = 0
    for w in q_words:
        if w in r.get("title", "").lower():
            score += 3
        if w in r.get("snippet", "").lower():
            score += 2
        if w in haystack:
            score += 1
    return score


def web_fetch(ctx: ToolContext, url: str) -> str:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return f"[web_fetch] 无效 URL (仅支持 http/https): {url!r}"

    allow_loopback = bool(ctx.config("tools.web.allow_loopback", False))
    blocked = _check_ssrf(url, allow_loopback=allow_loopback)
    if blocked:
        return blocked

    timeout = ctx.config("tools.web.timeout", 30)
    headers = {
        "User-Agent": "qingxiaotuan/0.1 (+https://github.com/qingxiaotuan)",
        "Accept-Encoding": "gzip, deflate",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    redirect_handler = _SafeRedirectHandler()
    redirect_handler.allow_loopback = allow_loopback
    opener = urllib.request.build_opener(redirect_handler)
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                return f"[web_fetch] 响应体过大 (>2MB): {url}"
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding.lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    try:
                        raw = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
                    except zlib.error:
                        pass
            elif encoding.lower() == "deflate":
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    pass
            content_type = resp.headers.get("Content-Type", "")
            text = _decode_body(raw, content_type, raw[:2000].decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return f"[web_fetch] HTTP 错误 {exc.code} {exc.reason} · {url}"
    except urllib.error.URLError as exc:
        return f"[web_fetch] 无法访问 {url}: {exc.reason}"
    except TimeoutError:
        return f"[web_fetch] 超时 ({timeout}s): {url}"
    except Exception as exc:  # noqa: BLE001
        return f"[web_fetch] 抓取失败: {type(exc).__name__}: {exc}"

    text = _TAG_RE.sub(" ", text)
    text = _HTML_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    max_fetch = int(ctx.config("network.fetch_max_chars", MAX_PAGE))
    if max_fetch > 0 and len(text) > max_fetch:
        text = text[:max_fetch] + "...[截断]"
    return text or "(页面为空)"


class WebPlugin(Plugin):
    name = "tools.web"
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("tools.web.enabled", True):
            return
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="web_fetch",
            description="抓取 URL 返回纯文本 (自动处理 gzip/编码/超时)",
            parameters={
                "type": "object",
                "properties": {"url": string_prop("http/https URL")},
                "required": ["url"],
            },
            handler=web_fetch, group="web", read_only=True,
        ))
        registry.register(Tool(
            name="web_search",
            description="联网搜索 (DuckDuckGo), 返回标题/URL/摘要 (自动分页+去重+相关度top-k)",
            parameters={
                "type": "object",
                "properties": {
                    "query": string_prop("搜索关键词"),
                    "max_results": {"type": "integer", "description": "返回条数 (1-500, 默认 5)"},
                },
                "required": ["query"],
            },
            handler=web_search, group="web", read_only=True,
        ))
