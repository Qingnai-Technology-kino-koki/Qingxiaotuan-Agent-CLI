"""MCP 结果转 kernel 输出 —— 把 MCP 返回值归一为统一数据结构。

- ``convert_content_block``：text / image(base64->image_url) / audio / resource /
  resource_link -> ``ContentPart``；超 10MB 的二进制部分丢弃。
- ``result_to_output``：媒体包裹、structuredContent/_meta 处理、单文本折叠回字符串。
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from ..contract import ContentPart
from .types import MCPContentBlock, MCPToolResult

MCP_MAX_BINARY_PART_BYTES = 10 * 1024 * 1024
MCP_MAX_BINARY_PART_CHARS = math.ceil((MCP_MAX_BINARY_PART_BYTES * 4) / 3)


def _dropped(reason: str) -> ContentPart:
    return ContentPart("text", text=f"[MCP content dropped: {reason}]")


def convert_content_block(block: MCPContentBlock) -> ContentPart:
    """把单个 MCP 内容块映射为 kernel ``ContentPart``。"""
    t = block.type

    if t == "text" and block.text:
        return ContentPart("text", text=block.text)

    if t == "image" and block.data:
        mime = block.mimeType or "image/png"
        return ContentPart("image_url", image_url={"url": f"data:{mime};base64,{block.data}"})

    if t == "audio" and block.data:
        mime = block.mimeType or "audio/mpeg"
        fmt = mime.split("/", 1)[-1]
        return ContentPart("input_audio", input_audio={"data": block.data, "format": fmt})

    if t == "resource" and isinstance(block.resource, dict):
        res = block.resource
        if res.get("text") is not None:
            return ContentPart("text", text=res["text"])
        if res.get("blob") is not None:
            mime = res.get("mimeType") or "application/octet-stream"
            blob = res["blob"]
            if mime.startswith("image/"):
                return ContentPart("image_url", image_url={"url": f"data:{mime};base64,{blob}"})
            if mime.startswith("audio/"):
                fmt = mime.split("/", 1)[-1]
                return ContentPart("input_audio", input_audio={"data": blob, "format": fmt})
            return _dropped(f"resource blob with unsupported mimeType '{mime}'")
        return _dropped(f"resource (uri: {res.get('uri')}) carried no text or blob payload")

    if t == "resource_link" and block.uri:
        mime = block.mimeType or "application/octet-stream"
        if mime.startswith("image/"):
            # 简单模型接受度判断省略，直接内联图片 url
            return ContentPart("image_url", image_url={"url": block.uri})
        if mime.startswith("audio/"):
            return _dropped(f"resource_link audio cannot be inlined: {block.uri}")
        return _dropped(f"resource_link with unsupported mimeType '{mime}'")

    return _dropped(f"content block of unsupported type '{t}'")


def _apply_binary_cap(parts: List[ContentPart]):
    out: List[ContentPart] = []
    truncated = False
    notices: List[str] = []
    for p in parts:
        if p.type == "text":
            out.append(p)
            continue
        url: str = ""
        if p.type == "image_url":
            url = (p.image_url or {}).get("url", "")
        elif p.type == "input_audio":
            url = (p.input_audio or {}).get("data", "")
        if url and len(url) > MCP_MAX_BINARY_PART_CHARS:
            approx_mb = (len(url) * 3 / 4) / (1024 * 1024)
            cap_mb = MCP_MAX_BINARY_PART_BYTES / (1024 * 1024)
            notice = (
                f"[binary part dropped: ~{approx_mb:.1f} MB exceeds "
                f"{cap_mb:.0f} MB per-part limit]"
            )
            out.append(ContentPart("text", text=notice))
            notices.append(notice)
            truncated = True
            continue
        out.append(p)
    return out, truncated, notices


def _collapse_single_text(parts: List[ContentPart]):
    if len(parts) == 1 and parts[0].type == "text":
        return parts[0].text
    return parts


def result_to_output(result: MCPToolResult, qualified_tool_name: str) -> Dict[str, Any]:
    """把 MCP 调用结果转换为 koong 输出字典 ``{output, is_error, note, truncated}``。

    output 为 ``str``（单文本折叠）或 ``List[ContentPart]``（多块/媒体）。
    """
    parts = [convert_content_block(b) for b in result.content]
    parts, truncated, notices = _apply_binary_cap(parts)

    has_text = any(p.type == "text" and p.text and p.text.strip() for p in parts)

    extras: Dict[str, Any] = {}
    if result.structuredContent is not None and not has_text:
        extras["structuredContent"] = result.structuredContent
    if result.meta is not None:
        meta = {k: v for k, v in result.meta.items() if not _is_reserved_meta_key(k)}
        if meta:
            extras["_meta"] = meta

    if extras:
        serialized = json.dumps(extras, ensure_ascii=False).replace("</mcp-result-extras>", "")
        parts.append(
            ContentPart(
                "text",
                text=f"\n<mcp-result-extras>\n{serialized}\n</mcp-result-extras>",
            )
        )

    output = _collapse_single_text(parts)
    note = "\n".join(notices) if notices else None
    return {
        "output": output,
        "is_error": result.isError,
        "note": note,
        "truncated": truncated if truncated else None,
    }


def _is_reserved_meta_key(key: str) -> bool:
    slash = key.find("/")
    if slash <= 0:
        return False
    labels = key[:slash].split(".")
    return any(
        (label == "modelcontextprotocol" or label == "mcp") and i < len(labels) - 1
        for i, label in enumerate(labels)
    )
