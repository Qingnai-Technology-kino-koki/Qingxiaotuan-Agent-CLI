"""Cursor pagination query and page response types.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .error_codes import ErrorCode

T = TypeVar("T")


@dataclass
class CursorQuery:
    before_id: str | None = None
    after_id: str | None = None
    page_size: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CursorQuery":
        before_id = raw.get("before_id")
        after_id = raw.get("after_id")
        if before_id is not None and after_id is not None:
            raise ValueError("before_id and after_id are mutually exclusive")
        page_size = raw.get("page_size")
        if page_size is not None:
            if not isinstance(page_size, int) or isinstance(page_size, bool):
                raise ValueError("page_size must be an integer")
            if not (1 <= page_size <= 100):
                raise ValueError("page_size must be between 1 and 100")
        if before_id is not None and not isinstance(before_id, str):
            raise ValueError("before_id must be a string")
        if after_id is not None and not isinstance(after_id, str):
            raise ValueError("after_id must be a string")
        return cls(before_id=before_id, after_id=after_id, page_size=page_size)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.before_id is not None:
            out["before_id"] = self.before_id
        if self.after_id is not None:
            out["after_id"] = self.after_id
        if self.page_size is not None:
            out["page_size"] = self.page_size
        return out


@dataclass
class PageResponse(Generic[T]):
    items: list[T] = field(default_factory=list)
    has_more: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"items": self.items, "has_more": self.has_more}
