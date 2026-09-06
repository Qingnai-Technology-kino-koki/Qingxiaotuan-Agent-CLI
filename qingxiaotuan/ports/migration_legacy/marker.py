"""Migration marker persistence and prompt-suppression decisions.

The marker (``<sourceHome>/.migrated-to-kimi-code``) records that a legacy
``~/.kimi/`` install has already been migrated to a kimi-code ``~/.kimi-code``
home. :func:`should_suppress_migration` decides whether the first-launch prompt
should be skipped for a single target home.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from .paths import migrated_marker, skip_marker

MARKER_VERSION = 1


@dataclass
class MarkerRun:
    started_at: str
    completed_at: str
    migrator_version: str
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarkerData:
    version: int
    first_migrated_at: str
    last_migrated_at: str
    migrator_version: str
    target_path: str
    runs: list[MarkerRun]
    target_paths: Optional[list[str]] = None


@dataclass
class MigrationSuppressionInput:
    source_home: str
    target_home: str


def _is_windows_abs(p: str) -> bool:
    # Drive-letter or UNC path, matching Node's path.win32.isAbsolute.
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        return True
    return p.startswith("\\\\") or p.startswith("//")


def _same_target_path(left: str, right: str) -> bool:
    if sys.platform == "win32":
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right)
        )

    left_win = _is_windows_abs(left)
    right_win = _is_windows_abs(right)
    if left_win or right_win:
        if not (left_win and right_win):
            return False
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right)
        )
    return os.path.normpath(os.path.abspath(left)) == os.path.normpath(
        os.path.abspath(right)
    )


def _marker_target_paths(marker: dict[str, Any]) -> list[str]:
    target_paths: list[str] = []
    raw_paths = marker.get("target_paths")
    if isinstance(raw_paths, list):
        for tp in raw_paths:
            if isinstance(tp, str):
                target_paths.append(tp)
    target_path = marker.get("target_path")
    if isinstance(target_path, str):
        if not any(_same_target_path(tp, target_path) for tp in target_paths):
            target_paths.append(target_path)
    return target_paths


def _append_target_path(target_paths: list[str], target_path: str) -> list[str]:
    if any(_same_target_path(tp, target_path) for tp in target_paths):
        return list(target_paths)
    return [*target_paths, target_path]


def should_suppress_migration(input: MigrationSuppressionInput) -> bool:
    """Decide whether the migration prompt should be suppressed for one target.

    A completed marker covers every target recorded in ``target_paths``; the
    legacy ``target_path`` field remains authoritative when that list is absent.
    Unreadable markers are treated conservatively as completed so upgrading does
    not start prompting users who had already dismissed the migration.
    """
    if os.path.exists(skip_marker(input.target_home)):
        return True

    marker_path = migrated_marker(input.source_home)
    if not os.path.exists(marker_path):
        return False

    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        if not isinstance(parsed, dict):
            return True
        target_paths = _marker_target_paths(parsed)
        if len(target_paths) == 0:
            return True
        return any(
            _same_target_path(tp, input.target_home) for tp in target_paths
        )
    except (OSError, json.JSONDecodeError):
        return True


def read_marker(source_home: str) -> Optional[MarkerData]:
    """Read and validate a marker, or ``None`` if absent/corrupt."""
    try:
        with open(migrated_marker(source_home), "r", encoding="utf-8") as f:
            parsed = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("version") != MARKER_VERSION:
        return None
    raw_runs = parsed.get("runs")
    if not isinstance(raw_runs, list):
        # A partially-written/hand-edited marker may keep `version` but lack a
        # valid `runs` array; treat it as absent to avoid appendMarkerRun
        # throwing and aborting a healthy rerun.
        return None
    runs = [
        MarkerRun(
            started_at=r["started_at"],
            completed_at=r["completed_at"],
            migrator_version=r["migrator_version"],
            summary=r.get("summary", {}),
        )
        for r in raw_runs
        if isinstance(r, dict)
    ]
    return MarkerData(
        version=MARKER_VERSION,
        first_migrated_at=parsed["first_migrated_at"],
        last_migrated_at=parsed["last_migrated_at"],
        migrator_version=parsed["migrator_version"],
        target_path=parsed["target_path"],
        runs=runs,
        target_paths=parsed.get("target_paths"),
    )


def write_marker(
    source_home: str,
    run: MarkerRun,
    target_path: str,
) -> None:
    """Write a brand-new marker with a single run."""
    data: dict[str, Any] = {
        "version": MARKER_VERSION,
        "first_migrated_at": run.started_at,
        "last_migrated_at": run.completed_at,
        "migrator_version": run.migrator_version,
        "target_path": target_path,
        "target_paths": [target_path],
        "runs": [
            {
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "migrator_version": run.migrator_version,
                "summary": run.summary,
            }
        ],
    }
    with open(migrated_marker(source_home), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def append_marker_run(
    source_home: str,
    run: MarkerRun,
    target_path: str,
) -> None:
    """Append a run to an existing marker; raises if no marker exists."""
    existing = read_marker(source_home)
    if existing is None:
        raise ValueError("appendMarkerRun: no existing marker")

    existing_target_paths = list(existing.target_paths or [])
    updated: dict[str, Any] = {
        **{
            "version": existing.version,
            "first_migrated_at": existing.first_migrated_at,
            "migrator_version": existing.migrator_version,
            "target_path": existing.target_path,
            "target_paths": existing_target_paths,
            "runs": [
                {
                    "started_at": r.started_at,
                    "completed_at": r.completed_at,
                    "migrator_version": r.migrator_version,
                    "summary": r.summary,
                }
                for r in existing.runs
            ],
        },
        "last_migrated_at": run.completed_at,
        "migrator_version": run.migrator_version,
        "target_path": target_path,
        "target_paths": _append_target_path(existing_target_paths, target_path),
        "runs": [
            *[
                {
                    "started_at": r.started_at,
                    "completed_at": r.completed_at,
                    "migrator_version": r.migrator_version,
                    "summary": r.summary,
                }
                for r in existing.runs
            ],
            {
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "migrator_version": run.migrator_version,
                "summary": run.summary,
            },
        ],
    }
    with open(migrated_marker(source_home), "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)
