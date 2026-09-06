"""Exceptions for the transcript port."""

from __future__ import annotations


class TranscriptDecodeError(ValueError):
    """Raised when transcript JSON/dict data is malformed and cannot be decoded."""

    def __init__(self, message: str, *, path: str = ""):
        super().__init__(message if not path else f"{path}: {message}")
        self.path = path
