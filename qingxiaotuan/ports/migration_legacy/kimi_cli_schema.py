"""Parsers for legacy kimi-cli persisted state.

Mirrors kimi-cli's ``Metadata`` (metadata.py) and ``SessionState``
(session_state.py) shapes. We validate the known subset and otherwise preserve
extra fields (the TS zod schemas use ``.passthrough()``), so a newer kimi-cli's
extra persisted keys do not break the port.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class OldWorkDirMeta:
    path: str
    kaos: str = "local"
    last_session_id: Optional[str] = None


@dataclass
class OldKimiJson:
    work_dirs: list[OldWorkDirMeta] = field(default_factory=list)


class SchemaError(ValueError):
    """Raised when legacy state does not match the expected shape."""


def parse_kimi_json(text: str) -> dict[str, Any]:
    """Parse a legacy ``kimi.json`` string.

    Returns a dict with a normalized ``work_dirs`` list. Each entry is a dict
    with ``path`` (str, required), ``kaos`` (str, defaults to ``"local"``) and
    ``last_session_id`` (str | None). Raises :class:`SchemaError` on invalid
    input.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"kimi.json is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise SchemaError("kimi.json must be a JSON object")
    raw_dirs = data.get("work_dirs", [])
    if not isinstance(raw_dirs, list):
        raise SchemaError("kimi.json 'work_dirs' must be an array")

    work_dirs: list[dict[str, Any]] = []
    for wd in raw_dirs:
        if not isinstance(wd, dict):
            raise SchemaError("each work_dir must be an object")
        path = wd.get("path")
        if not isinstance(path, str):
            raise SchemaError("each work_dir must have a string 'path'")
        work_dirs.append(
            {
                "path": path,
                "kaos": wd.get("kaos", "local"),
                "last_session_id": wd.get("last_session_id", None),
            }
        )
    return {"work_dirs": work_dirs}


def parse_session_state(text: str) -> dict[str, Any]:
    """Parse a legacy ``state.json`` string.

    The legacy schema tolerates many optional fields, so this accepts any JSON
    object and returns it verbatim (passthrough). ``None`` is normalized to an
    empty dict. Raises :class:`SchemaError` when the payload is not an object.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"session state is not valid JSON: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SchemaError("session state must be a JSON object")
    return data
