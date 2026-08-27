"""ImageRef: 与模型无关的图片引用。

一张图片只描述一次 (来源/媒体类型/字节/原始路径), 由 builder 决定如何塞进
发给模型的 content 块。本地文件在编码时一次性读取为字节并 base64, 之后不再
触碰磁盘; 远程 URL 原样透传给支持自拉取的模型端点。
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

# 默认单图体积上限: 15MB (base64 后约 20MB)。超过即拒绝, 保护上下文预算。
default_max_bytes: int = 15 * 1024 * 1024

# 文件头魔数 → 媒体类型。先按字节判定, 再退回扩展名猜测。
_MAGIC: List[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
]

_SUPPORTED_PREFIXES = ("image/",)


@dataclass
class ImageRef:
    """一张图片的不可变描述。"""

    media_type: str
    source: str = "file"            # file | url | data
    data: Optional[bytes] = None    # 本地/数据图片的解码字节
    url: Optional[str] = None       # 远程 URL 或 data: URI
    path: Optional[str] = None      # 原始路径 (展示/降级注记用)

    def data_uri(self) -> str:
        """返回可直接放进 image_url 块的 URI。"""
        if self.url is not None and self.url.startswith("data:"):
            return self.url
        if self.url is not None:
            return self.url  # 远程 http(s), 由模型端点拉取
        if self.data is not None:
            b64 = base64.b64encode(self.data).decode("ascii")
            return f"data:{self.media_type};base64,{b64}"
        raise ValueError("ImageRef 既无 data 也无 url, 无法生成 URI")

    def is_remote(self) -> bool:
        return bool(self.url) and not self.url.startswith("data:")  # type: ignore[union-attr]

    def byte_size(self) -> int:
        return len(self.data) if self.data is not None else 0


def _detect_media_type(path: str, data: bytes) -> str:
    """先按字节魔数判定, 再退回扩展名猜测; 非图片返回 application/octet-stream。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mt in _MAGIC:
        if data[: len(magic)] == magic:
            return mt
    guessed, _ = mimetypes.guess_type(path)
    if guessed:
        return guessed
    return "application/octet-stream"


def encode_image_file(path: str, workspace: Optional[str] = None,
                      max_bytes: int = default_max_bytes) -> ImageRef:
    """读取本地图片文件并编码为 ImageRef。"""
    p = Path(path)
    if not p.is_absolute() and workspace:
        cand = Path(workspace) / path
        if cand.exists():
            p = cand
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {path}")
    if p.is_dir():
        raise IsADirectoryError(f"不是图片文件 (是目录): {path}")
    data = p.read_bytes()
    if len(data) > max_bytes:
        raise ValueError(
            f"图片过大 ({len(data) // 1024}KB), 上限 {max_bytes // 1024}KB"
        )
    mt = _detect_media_type(str(p), data)
    if not mt.startswith(_SUPPORTED_PREFIXES):
        raise ValueError(f"不支持的图片类型: {mt}")
    return ImageRef(media_type=mt, source="file", data=data, path=str(p))


def encode_image_url(url: str) -> ImageRef:
    """远程图片: 原样保留 URL, 由模型端点拉取 (不下载到本地)。"""
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"非法的图片 URL: {url}")
    return ImageRef(media_type="image/*", source="url", url=url, path=url)


def encode_image_data_uri(uri: str) -> ImageRef:
    """data: 形式的图片 URI, 直接透传。"""
    if not uri.startswith("data:"):
        raise ValueError("不是 data: URI")
    meta = uri[len("data:"):].split(";", 1)[0] or "application/octet-stream"
    return ImageRef(media_type=meta, source="data", url=uri, path=None)


def encode_image_source(spec: str, workspace: Optional[str] = None,
                        max_bytes: int = default_max_bytes) -> ImageRef:
    """按规格自动分流: URL / data: URI / 本地文件。"""
    if spec.startswith("http://") or spec.startswith("https://"):
        return encode_image_url(spec)
    if spec.startswith("data:"):
        return encode_image_data_uri(spec)
    return encode_image_file(spec, workspace, max_bytes)
