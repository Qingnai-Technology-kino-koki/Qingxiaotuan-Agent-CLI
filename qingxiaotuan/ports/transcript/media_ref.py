"""Media path tag / daemon file ref helpers (对齐上游 contract/mediaRef 的语义)."""

from __future__ import annotations

import re
from typing import Optional

MediaPathTagKind = str  # 'image' | 'video' | 'audio' | 'file'


class MediaPathTagMatch:
    def __init__(self, kind: str, path: str):
        self.kind = kind
        self.path = path


_SINGLE_MEDIA_PATH_TAG_RE = re.compile(
    r'^\s*<(image|video|audio|file)\b[^>]*?\bpath="([^"]*)"[^>]*>(?:</\1>)?\s*$'
)


def match_media_path_tag_text(text: str) -> Optional[MediaPathTagMatch]:
    match = _SINGLE_MEDIA_PATH_TAG_RE.match(text)
    if match is None:
        return None
    return MediaPathTagMatch(kind=match.group(1), path=_unescape_media_attribute(match.group(2)))


def _unescape_media_attribute(value: str) -> str:
    return (
        value.replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


KIMI_FILE_SCHEME = "kimi-file://"


class DaemonFileRef:
    def __init__(self, file_id: str):
        self.file_id = file_id


def parse_daemon_file_ref(url: str) -> Optional[DaemonFileRef]:
    if not url.startswith(KIMI_FILE_SCHEME):
        return None
    rest = url[len(KIMI_FILE_SCHEME):]
    query_at = rest.find("?")
    file_id = rest if query_at == -1 else rest[:query_at]
    return DaemonFileRef(file_id) if len(file_id) > 0 else None


def parse_daemon_file_ref_file_id(url: str) -> Optional[str]:
    ref = parse_daemon_file_ref(url)
    return ref.file_id if ref is not None else None


class MediaRefPart:
    def __init__(self, part_type: str, text: Optional[str] = None, image_url: Optional[dict] = None, video_url: Optional[dict] = None):
        self.type = part_type
        self.text = text
        self.image_url = image_url
        self.video_url = video_url


def daemon_file_ref_from_pairing_part(part: MediaRefPart):
    if part.type != "image_url" and part.type != "video_url":
        return None
    url = part.image_url.get("url") if part.type == "image_url" else (part.video_url.get("url") if part.video_url else None)
    if not isinstance(url, str):
        return None
    ref = parse_daemon_file_ref(url)
    if ref is None:
        return None
    return {"kind": "image" if part.type == "image_url" else "video", "ref": ref}
