"""builder: 把文本 + 图片按模型视觉能力, 构造发给模型的 content。

中间表示统一为 OpenAI 风格块 ({type: text|image_url})。Anthropic 适配器在
_convert_messages 里把 image_url 翻译为本地 image source 块, 因此这里不区分后端。

核心门控: 模型不支持视觉时, 绝不发送任何图片字节, 仅把路径以文本注记送达,
避免"静默失明" (模型以为收到图其实没有)。
"""

from __future__ import annotations

from typing import Any, List, Optional, Union

from .blocks import ImageRef


def _image_block(ref: ImageRef) -> dict:
    return {"type": "image_url", "image_url": {"url": ref.data_uri()}}


def build_user_content(
    text: str,
    images: List[ImageRef],
    vision_capable: bool,
    provider: str = "openai",
) -> Union[str, List[dict]]:
    """构造一条 user 消息的 content。

    - 无图: 返回纯文本 str (保持与原管线兼容)。
    - 有图且支持视觉: 返回 [text 块, image_url 块, ...]。
    - 有图但不支持视觉: 降级为文本 + 路径注记 (图片不送达)。
    """
    if not images:
        return text
    if not vision_capable:
        paths = ", ".join(r.path or r.url or "<data>" for r in images)
        return (
            text
            + f"\n\n[注意: 当前模型不支持视觉, 以下图片未送达模型, 仅记录路径: {paths}]"
        )
    blocks: List[dict] = [{"type": "text", "text": text}]
    for ref in images:
        blocks.append(_image_block(ref))
    return blocks


def build_tool_content(
    text: str,
    images: List[ImageRef],
    vision_capable: bool,
) -> Union[str, List[dict]]:
    """构造工具结果消息的 content (工具返回图片时)。

    工具返回图片且模型支持视觉: 文本块 + 图片块; 否则仅文本。
    """
    if not images or not vision_capable:
        return text
    blocks: List[dict] = [{"type": "text", "text": text}]
    for ref in images:
        blocks.append(_image_block(ref))
    return blocks
