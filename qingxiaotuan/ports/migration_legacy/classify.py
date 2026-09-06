"""Classify a legacy kimi-cli session directory and analyze its context.

Ported from ``sessions/classify.ts`` and the ``analyzeContextContent`` helper in
``sessions/translator.ts``. The classification decides whether a session is
migratable, an unused/cleared session, or malformed.
"""

from __future__ import annotations

import json
import os
from typing import Literal

# Roles that carry migratable conversation content.
USABLE_ROLES = frozenset({"user", "assistant", "tool"})

# Marker-only roles a cleared session may carry.
DROPPED_ROLES = frozenset({"_system_prompt", "_checkpoint", "_usage"})

SessionClass = Literal["placeholder", "empty", "malformed", "real"]
ContextContent = Literal["real", "empty", "corrupt"]


def analyze_context_content(lines: list[str]) -> ContextContent:
    """Classify a ``context.jsonl`` payload by scanning its lines.

    - ``'real'``    -- has at least one user/assistant/tool row.
    - ``'empty'``   -- parses, but only markers or blank lines.
    - ``'corrupt'`` -- every non-blank line failed to parse (disk damage).
    """
    had_parseable_line = False
    had_any_non_blank = False
    for raw_line in lines:
        line = raw_line.strip()
        if line == "":
            continue
        had_any_non_blank = True
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        had_parseable_line = True
        role = parsed.get("role")
        if isinstance(role, str) and role in USABLE_ROLES:
            return "real"
    if had_any_non_blank and not had_parseable_line:
        return "corrupt"
    return "empty"


def classify_session_dir(session_dir: str) -> SessionClass:
    """Classify a single legacy session directory."""
    try:
        entries = os.listdir(session_dir)
    except OSError:
        return "malformed"
    if len(entries) == 0:
        return "empty"
    if len(entries) == 1 and entries[0] == "test":
        return "placeholder"
    # migrateOneSession hard-fails without context.jsonl, so a dir lacking it is
    # not migratable.
    if "context.jsonl" not in entries:
        return "malformed"

    context_path = os.path.join(session_dir, "context.jsonl")
    try:
        with open(context_path, "r", encoding="utf-8") as f:
            context_text = f.read()
    except OSError:
        return "malformed"

    content = analyze_context_content(context_text.split("\n"))
    if content in ("real", "corrupt"):
        return "real"
    return "empty"
