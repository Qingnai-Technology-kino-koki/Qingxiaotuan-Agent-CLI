"""Append-only cross-run migration diagnostic log.

Each call contributes one block prefixed by a timestamped header. A run with
failures appends per-session diagnostics (source path, reason, and a
``context.jsonl`` line-count + role histogram). A run with no failures appends a
one-line ``no failures.`` marker -- the file therefore captures the complete
history of every migration attempt. Best-effort: a finished migration must not
be turned into a failure by a log write error, so all I/O is guarded.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .paths import migration_errors_log_file


@dataclass
class MigrationFailureEntry:
    source_path: str
    reason: str


@dataclass
class MigrationErrorsLogInput:
    started_at: str
    failures: list[MigrationFailureEntry] = field(default_factory=list)


def write_migration_errors_log(target_home: str, input: MigrationErrorsLogInput) -> None:
    lines: list[str] = [f"===== migration run @ {input.started_at} ====="]

    if len(input.failures) == 0:
        lines.extend(["no failures.", ""])
    else:
        lines.append(f"{len(input.failures)} session(s) failed to migrate.")
        lines.append("")
        for index, failure in enumerate(input.failures, start=1):
            lines.extend(
                [
                    f"[{index}] {os.path.basename(failure.source_path)}",
                    f"  source: {failure.source_path}",
                    f"  reason: {failure.reason}",
                    f"  {_describe_context(failure.source_path)}",
                    "",
                ]
            )

    try:
        os.makedirs(target_home, mode=0o700, exist_ok=True)
        # A single appendFile is atomic enough; mode only applies on creation.
        with open(migration_errors_log_file(target_home), "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError:
        # Best-effort -- see the doc comment above.
        pass


def _describe_context(session_dir: str) -> str:
    context_path = os.path.join(session_dir, "context.jsonl")
    try:
        with open(context_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return "context.jsonl: unreadable"

    lines = [ln for ln in text.split("\n") if ln.strip() != ""]
    role_counts: dict[str, int] = {}
    for line in lines:
        role = "<unparseable>"
        try:
            parsed: Any = json.loads(line)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict):
                raw = parsed.get("role")
                role = raw if isinstance(raw, str) else "<no-role>"
        role_counts[role] = role_counts.get(role, 0) + 1

    histogram = " ".join(
        f"{role}={count}"
        for role, count in sorted(
            role_counts.items(), key=lambda kv: kv[1], reverse=True
        )
    )
    return f"context.jsonl: {len(lines)} lines" + (
        f" - {histogram}" if histogram else ""
    )
