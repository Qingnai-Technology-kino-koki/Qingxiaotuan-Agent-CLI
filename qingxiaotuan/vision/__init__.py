"""多模态视觉引擎。

把"用户/工具产生的图片"抽象成与模型无关的 ImageRef, 再由 builder 按模型能力
(vision) 构造 OpenAI 风格的 content 块。Anthropic 适配器在 _convert_messages 里
把 image_url 块翻译成本地的 image source 块, 因此本模块只产出一种中间表示。

设计要点 (对标 Claude Code 的视觉能力, 并更克制):
- 图片来源分三类: 本地文件 (读入后 base64)、远程 URL (模型自行拉取)、data URI。
- 强制校验文件类型与体积上限 (默认 15MB), 防止把几 GB 的误传文件塞进上下文。
- 非视觉模型不发送任何图片字节, 仅把路径作为文本注记送达, 避免静默失明。
"""

from .blocks import (
    ImageRef,
    encode_image_source,
    encode_image_file,
    encode_image_url,
    default_max_bytes,
)
from .builder import build_user_content, build_tool_content

__all__ = [
    "ImageRef",
    "encode_image_source",
    "encode_image_file",
    "encode_image_url",
    "build_user_content",
    "build_tool_content",
    "default_max_bytes",
]
