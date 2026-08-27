"""Web 工具插件: 抓取网页并转成可读文本。

健壮性要点 (工具层契约: 工具崩溃绝不能炸 Agent 循环):
- 所有网络/解码异常都被捕获, 返回友好错误字符串而非抛异常;
- 支持 gzip/deflate 响应体;
- 按 HTTP 响应头 charset 解码 (缺省 UTF-8, 错误字符替换), 对中文网页更友好;
- 超时 / URL 非法 / 非 http(s) 方案 都给出明确提示。
"""

from __future__ import annotations

import gzip
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib
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
    """联网搜索: DuckDuckGo HTML 版, 返回 'N. 标题/URL/摘要' 列表。"""
    if not isinstance(query, str) or not query.strip():
        return "[web_search] 搜索词不能为空"
    try:
        max_results = max(1, min(int(max_results), 10))
    except (TypeError, ValueError):
        max_results = 5
    timeout = float(ctx.config("tools.web.timeout", 30))
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    try:
        html = _ddg_fetch(url, timeout)
    except Exception as exc:  # noqa: BLE001
        return f"[web_search] 搜索失败 ({type(exc).__name__}): 请检查网络后重试"
    results = parse_ddg_results(html, max_results)
    if not results:
        return f'[web_search] 未找到与 "{query}" 相关的结果'
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
    return "\n".join(lines)


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
    if len(text) > MAX_PAGE:
        text = text[:MAX_PAGE] + "...[截断]"
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
            description="联网搜索 (DuckDuckGo), 返回标题/URL/摘要列表",
            parameters={
                "type": "object",
                "properties": {
                    "query": string_prop("搜索关键词"),
                    "max_results": {"type": "integer", "description": "返回条数 (1-10, 默认 5)"},
                },
                "required": ["query"],
            },
            handler=web_search, group="web", read_only=True,
        ))
