"""Extract a human-readable error message from an arbitrary API error payload.

Ported from ``api-error.ts``. The async ``readApiErrorMessage`` wrapper that
consumes a ``fetch`` ``Response`` is intentionally omitted -- it requires a
network/Web-fetch surface and lives behind ``SKIPPED.md``. Callers that already
hold parsed JSON pass it straight to :func:`extract_api_error_message`.
"""

from __future__ import annotations

from typing import Any

from .utils import is_record, non_empty_string

_DIRECT_ERROR_KEYS = ("error_description", "message", "detail")
_NESTED_ERROR_KEYS = ("message", "error_description", "detail", "code", "type")


def extract_api_error_message(value: Any) -> str | None:
    """Best-effort walk of a (possibly nested) API error payload.

    Handles arrays, direct string fields, an ``error`` string or nested object,
    and an ``errors`` array -- mirroring the TypeScript source. Returns the first
    non-empty message found, or ``None``.
    """
    if isinstance(value, list):
        for item in value:
            message = extract_api_error_message(item)
            if message is not None:
                return message
        return None

    if not is_record(value):
        return None

    for key in _DIRECT_ERROR_KEYS:
        message = non_empty_string(value.get(key))
        if message is not None:
            return message

    error = value.get("error")
    error_string = non_empty_string(error)
    if error_string is not None:
        return error_string

    if is_record(error):
        for key in _NESTED_ERROR_KEYS:
            message = non_empty_string(error.get(key))
            if message is not None:
                return message

    errors = value.get("errors")
    if isinstance(errors, list):
        for item in errors:
            message = extract_api_error_message(item)
            if message is not None:
                return message

    return None


__all__ = ["extract_api_error_message"]
