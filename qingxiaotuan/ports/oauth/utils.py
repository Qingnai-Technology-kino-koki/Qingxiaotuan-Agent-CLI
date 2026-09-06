"""Small pure helpers shared across the OAuth port."""

from __future__ import annotations

from typing import Any


def is_record(value: Any) -> bool:
    """Return True when *value* is a JSON object (dict), not a list or scalar."""
    return isinstance(value, dict)


def non_empty_string(value: Any) -> str | None:
    """Return *value* trimmed if it is a non-empty string, else ``None``."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


__all__ = ["is_record", "non_empty_string"]
