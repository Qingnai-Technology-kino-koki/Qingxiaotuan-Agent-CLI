"""Agent 多模态视觉处理 —— 从 agent.py 提取的独立职责。

处理图片挂接、发送、内容构建。当模型不支持视觉时绝不发送图片字节。

用法:
    class Agent(VisionMixin, ...):
        pass

    agent.attach_image("/path/to/screenshot.png")
    agent.run("describe this image")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from ..vision.blocks import ImageRef
    from ..tools.base import ToolResult

log = logging.getLogger(__name__)


class VisionMixin:
    """多模态视觉处理: 图片挂接 + 内容构建。

    提供 attach_image / clear_pending_images / _tool_content 三个方法,
    Agent 类继承后在消息构建时使用。
    """

    # 这些属性由 Agent.__init__ 设置
    pending_images: List["ImageRef"]
    model: Any
    workspace: str
    config: Any

    def attach_image(self, spec: str) -> str:
        """挂接一张图片 (本地路径 / http(s) URL / data: URI), 随下一轮 user 消息发送。

        返回面向用户的提示文案。失败抛异常由调用方捕获。
        """
        from ..vision import encode_image_source

        max_mb = int(self.config.get("agent.vision_max_mb", 15))
        ref = encode_image_source(spec, self.workspace, max_mb * 1024 * 1024)
        self.pending_images.append(ref)
        if ref.is_remote():
            return f"已挂接远程图片: {spec}（模型支持视觉时将在下一轮送达）"
        return f"已挂接图片: {ref.path}（{ref.byte_size() // 1024}KB, {ref.media_type}）"

    def clear_pending_images(self) -> int:
        """清空待发送的图片缓冲, 返回清除的数量。"""
        n = len(self.pending_images)
        self.pending_images = []
        return n

    def _tool_content(self, result: Any) -> Any:
        """把工具返回结果转成 tool 消息的 content。

        工具返回图片且模型支持视觉 -> [text 块, image_url 块, ...];
        否则原样 (文字或 ToolResult, 由下游 str 化)。
        """
        from ..tools.base import ToolResult as _ToolResult
        from ..vision import build_tool_content

        if isinstance(result, _ToolResult) and result.images:
            vision = getattr(self.model.capabilities, "vision", False)
            if vision:
                return build_tool_content(result.content, result.images, vision)
        return result
