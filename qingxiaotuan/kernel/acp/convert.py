"""ACP ContentBlock <-> kernel ContentPart 互转。

自研实现 —— 内容块互转（精简版，去掉图片压缩/
MCP server / XML 转义等依赖项，保留协议映射核心；压缩步骤在青小团侧交由既有
provider 处理，本模块只做结构转换）。

ACP 侧 ``ContentBlock`` 形状（dict）：
- ``{"type": "text", "text": ...}``
- ``{"type": "image", "mimeType": ..., "data": <base64>}``
- ``{"type": "audio", ...}`` （青小团侧当前不支持，丢弃并警告）
- ``{"type": "resource_link", "uri": ..., "name": ...}``
- ``{"type": "resource", "resource": {"uri":..., "text":...} | {"uri":..., "blob":...}}``
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Dict, List, Union

from ..contract import ContentPart


def acp_blocks_to_content_parts(blocks: List[Dict[str, Any]]) -> List[ContentPart]:
    """ACP ContentBlock[] -> kernel ContentPart[]（用户 prompt 输入用）。"""
    out: List[ContentPart] = []
    for block in blocks or []:
        btype = block.get("type")
        if btype == "text":
            out.append(ContentPart(type="text", text=block.get("text", "")))
        elif btype == "image":
            mime = block.get("mimeType") or "image/png"
            data = block.get("data") or ""
            url = f"data:{mime};base64,{data}"
            out.append(ContentPart(type="image_url", image_url={"url": url}))
        elif btype == "audio":
            # 青小团 prompt 当前不支持音频，降级丢弃。
            continue
        elif btype == "resource_link":
            text = _file_link_to_text_ref(block.get("uri", "")) or (
                f'<resource_link uri="{block.get("uri", "")}" '
                f'name="{block.get("name", "")}" />'
            )
            out.append(ContentPart(type="text", text=text))
        elif btype == "resource":
            resource = block.get("resource") or {}
            if isinstance(resource, dict) and "text" in resource:
                out.append(
                    ContentPart(type="text", text=f'<resource uri="{resource.get("uri", "")}">{resource["text"]}</resource>')
                )
            # blob 资源降级丢弃。
        # 其它未知 block 类型：降级忽略，不影响主链路。
    return out


def content_parts_to_acp(parts: List[ContentPart]) -> List[Dict[str, Any]]:
    """kernel ContentPart[] -> ACP ContentBlock[]（回显 / 历史重放用）。"""
    out: List[Dict[str, Any]] = []
    for part in parts or []:
        if part.type == "text":
            out.append({"type": "text", "text": part.text or ""})
        elif part.type == "image_url" and part.image_url:
            url = part.image_url.get("url", "")
            mime, data = _split_data_url(url)
            if mime is not None:
                out.append({"type": "image", "mimeType": mime, "data": data})
            else:
                out.append({"type": "text", "text": url})
        # 音频/其它：降级为文本说明。
        else:
            out.append({"type": "text", "text": part.text or ""})
    return out


def tool_result_to_acp(output: Any) -> List[Dict[str, Any]]:
    """工具输出 -> ACP ToolCallContent[]（text 内容块）。

    工具结果转 ACP 内容块：字符串原样，对象/数组 JSON 化，
    None/空 -> 空列表（调用方仍会发 ``tool_call_update`` 表示状态切换）。
    """
    if output is None:
        return []
    if isinstance(output, str):
        return [{"type": "content", "content": {"type": "text", "text": output}}] if output else []
    try:
        text = json.dumps(output, ensure_ascii=False)
    except (TypeError, ValueError):
        text = "[object]"
    if not text:
        return []
    return [{"type": "content", "content": {"type": "text", "text": text}}]


# ----------------------------------------------------------------- 内部辅助
def _split_data_url(url: str) -> Union[tuple, tuple]:
    if not url.startswith("data:"):
        return (None, "")
    try:
        meta, data = url[5:].split(",", 1)
    except ValueError:
        return (None, "")
    mime = meta.split(";", 1)[0] or "image/png"
    return (mime, data)


def _file_link_to_text_ref(uri: str) -> Union[str, None]:
    """``file://`` 链接投影为本地路径文本（非 file 协议返回 None）。"""
    if not uri.startswith("file://"):
        return None
    path = uri[len("file://"):]
    # 去掉 Windows UNC / localhost 复杂度：保留路径主体即可。
    if path.startswith("/") and len(path) >= 3 and path[2] == ":":  # /C:/...
        path = path[1:]
    return path
