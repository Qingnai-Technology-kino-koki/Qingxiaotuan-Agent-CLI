"""Message role and content-block types.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .time import normalize_iso_date_time


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


@dataclass
class TextContent:
    type: str = "text"
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass
class ToolUseContent:
    type: str = "tool_use"
    tool_call_id: str = ""
    tool_name: str = ""
    input: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "input": self.input,
        }


@dataclass
class ToolResultContent:
    type: str = "tool_result"
    tool_call_id: str = ""
    output: Any = None
    is_error: Optional[bool] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.type,
            "tool_call_id": self.tool_call_id,
            "output": self.output,
        }
        if self.is_error is not None:
            out["is_error"] = self.is_error
        return out


@dataclass
class ImageSourceUrl:
    kind: str = "url"
    url: str = ""
    id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "url": self.url}
        if self.id is not None:
            out["id"] = self.id
        return out


@dataclass
class ImageSourceBase64:
    kind: str = "base64"
    media_type: str = ""
    data: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "media_type": self.media_type, "data": self.data}


@dataclass
class ImageSourceFile:
    kind: str = "file"
    file_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "file_id": self.file_id}


@dataclass
class ImageSourceSessionMedia:
    kind: str = "session_media"
    file_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "file_id": self.file_id}


@dataclass
class ImageSourcePath:
    kind: str = "path"
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path}


IMAGE_SOURCE_TYPES: dict[str, type] = {
    "url": ImageSourceUrl,
    "base64": ImageSourceBase64,
    "file": ImageSourceFile,
    "session_media": ImageSourceSessionMedia,
    "path": ImageSourcePath,
}


def parse_image_source(raw: dict[str, Any]) -> Any:
    kind = raw.get("kind")
    cls = IMAGE_SOURCE_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown image source kind: {kind!r}")
    required = {"url": ["url"], "base64": ["media_type", "data"], "file": ["file_id"],
                "session_media": ["file_id"], "path": ["path"]}
    kwargs: dict[str, Any] = {"kind": kind}
    for field_name in ("url", "id", "media_type", "data", "file_id", "path"):
        if field_name in raw:
            kwargs[field_name] = raw[field_name]
    for need in required.get(kind, []):  # type: ignore[arg-type]
        if not kwargs.get(need):
            raise ValueError(f"image source {kind} requires {need}")
    return cls(**kwargs)  # type: ignore[call-arg]


@dataclass
class ImageContent:
    type: str = "image"
    source: Any = None

    def to_dict(self) -> dict[str, Any]:
        src = self.source.to_dict() if hasattr(self.source, "to_dict") else self.source
        return {"type": self.type, "source": src}


@dataclass
class VideoContent:
    type: str = "video"
    source: Any = None

    def to_dict(self) -> dict[str, Any]:
        src = self.source.to_dict() if hasattr(self.source, "to_dict") else self.source
        return {"type": self.type, "source": src}


@dataclass
class FileContent:
    type: str = "file"
    file_id: Optional[str] = None
    path: Optional[str] = None
    name: Optional[str] = None
    media_type: Optional[str] = None
    size: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        for k in ("file_id", "path", "name", "media_type", "size"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


@dataclass
class ThinkingContent:
    type: str = "thinking"
    thinking: str = ""
    signature: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "thinking": self.thinking}
        if self.signature is not None:
            out["signature"] = self.signature
        return out


MESSAGE_CONTENT_TYPES: dict[str, type] = {
    "text": TextContent,
    "tool_use": ToolUseContent,
    "tool_result": ToolResultContent,
    "image": ImageContent,
    "video": VideoContent,
    "file": FileContent,
    "thinking": ThinkingContent,
}


def parse_message_content(raw: dict[str, Any]) -> Any:
    """Dispatch on ``type`` to the matching message content variant."""
    if not isinstance(raw, dict):
        raise ValueError("message content must be an object")
    kind = raw.get("type")
    cls = MESSAGE_CONTENT_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown message content type: {kind!r}")

    if cls is TextContent:
        return TextContent(text=raw.get("text", ""))
    if cls is ToolUseContent:
        return ToolUseContent(
            tool_call_id=raw.get("tool_call_id", ""),
            tool_name=raw.get("tool_name", ""),
            input=raw.get("input"),
        )
    if cls is ToolResultContent:
        return ToolResultContent(
            tool_call_id=raw.get("tool_call_id", ""),
            output=raw.get("output"),
            is_error=raw.get("is_error"),
        )
    if cls is ImageContent:
        return ImageContent(source=parse_image_source(raw["source"]) if "source" in raw else None)
    if cls is VideoContent:
        return VideoContent(source=parse_image_source(raw["source"]) if "source" in raw else None)
    if cls is FileContent:
        return _build_file_content(raw)
    if cls is ThinkingContent:
        return ThinkingContent(thinking=raw.get("thinking", ""), signature=raw.get("signature"))
    raise ValueError(f"unhandled message content type: {kind!r}")


def _build_file_content(raw: dict[str, Any]) -> FileContent:
    has_file_id = raw.get("file_id") is not None
    has_path = raw.get("path") is not None
    if has_file_id == has_path:
        raise ValueError("file content requires exactly one of file_id or path")
    if has_path:
        return FileContent(path=raw["path"])
    for key in ("name", "media_type", "size"):
        if raw.get(key) is None:
            raise ValueError(f"file content requires {key} with file_id")
    return FileContent(
        file_id=raw["file_id"],
        name=raw.get("name"),
        media_type=raw.get("media_type"),
        size=raw.get("size"),
    )


@dataclass
class Message:
    id: str
    session_id: str
    role: str
    content: list[Any]
    created_at: str
    prompt_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Message":
        for key in ("id", "session_id", "role"):
            if not isinstance(raw.get(key), str) or raw[key] == "":
                raise ValueError(f"message.{key} is required")
        role = raw["role"]
        try:
            MessageRole(role)
        except ValueError as exc:
            raise ValueError(f"invalid message role: {role!r}") from exc
        content = raw.get("content")
        if not isinstance(content, list):
            raise ValueError("message.content must be a list")
        parsed_content = [parse_message_content(c) for c in content]
        return cls(
            id=raw["id"],
            session_id=raw["session_id"],
            role=role,
            content=parsed_content,
            created_at=normalize_iso_date_time(raw["created_at"]),
            prompt_id=raw.get("prompt_id"),
            parent_message_id=raw.get("parent_message_id"),
            metadata=raw.get("metadata"),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.content],
            "created_at": self.created_at,
        }
        if self.prompt_id is not None:
            out["prompt_id"] = self.prompt_id
        if self.parent_message_id is not None:
            out["parent_message_id"] = self.parent_message_id
        if self.metadata is not None:
            out["metadata"] = self.metadata
        return out
