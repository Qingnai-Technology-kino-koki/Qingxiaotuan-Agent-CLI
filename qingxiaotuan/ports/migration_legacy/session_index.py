"""Append-only ``session_index.jsonl`` under the target home.

One JSON object per line. Used to map session ids back to their directories so
the session picker can locate migrated sessions.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class SessionIndexEntry:
    session_id: str
    session_dir: str
    work_dir: str


def _index_path(target_home: str) -> str:
    from .paths import target_session_index

    return target_session_index(target_home)


def append_session_index_entry(target_home: str, entry: SessionIndexEntry) -> None:
    path = _index_path(target_home)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry.__dict__) + "\n")


def ensure_session_index_entry(target_home: str, entry: SessionIndexEntry) -> None:
    """Idempotently ensure ``entry`` is present in the index.

    Appends the entry only when no existing line carries the same ``session_id``.
    Self-heals the index on a re-run that wrote the session directory but crashed
    before appending its index entry.
    """
    path = _index_path(target_home)
    if _has_session_index_entry(path, entry.session_id):
        return
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry.__dict__) + "\n")


def _has_session_index_entry(path: str, session_id: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    for line in text.split("\n"):
        if line == "":
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            # Skip malformed lines -- treat them as absent entries.
            continue
        if isinstance(parsed, dict) and parsed.get("session_id") == session_id:
            return True
    return False
