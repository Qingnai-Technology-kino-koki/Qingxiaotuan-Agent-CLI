"""Web 工具插件: 抓取网页并转成可读文本。

健壮性要点 (工具层契约: 工具崩溃绝不能炸 Agent 循环):
- 所有网络/解码异常都被捕获, 返回友好错误字符串而非抛异常;
- 支持 gzip/deflate 响应体;
- 按 HTTP 响应头 charset 解码 (缺省 UTF-8, 错误字符替换), 对中文网页更友好;
- 超时 / URL 非法 / 非 http(s) 方案 都给出明确提示。
"""

from __future__ import annotations

import gzip
import re
import urllib.error
import urllib.request
import zlib

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop

MAX_PAGE = 15000
_TAG_RE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.IGNORECASE)
_HTML_RE = re.compile(r"<[^>]+>")
_META_CHARSET_RE = re.compile(r'<meta[^>]+charset=["\']?([^"\'\s>]+)', re.IGNORECASE)


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


def web_fetch(ctx: ToolContext, url: str) -> str:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return f"[web_fetch] 无效 URL (仅支持 http/https): {url!r}"

    timeout = ctx.config("tools.web.timeout", 30)
    headers = {
        "User-Agent": "qingxiaotuan/0.1 (+https://github.com/qingxiaotuan)",
        "Accept-Encoding": "gzip, deflate",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
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
