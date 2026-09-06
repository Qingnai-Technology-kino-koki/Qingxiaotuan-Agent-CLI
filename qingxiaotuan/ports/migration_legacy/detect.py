"""Detect legacy kimi-cli state under a source ``~/.kimi/`` home.

Ported from ``detect.ts``. Produces a :class:`MigrationPlan` consumed by the
migration runner. Pure filesystem scanning; no side effects.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from .classify import classify_session_dir
from .kimi_cli_schema import parse_kimi_json, parse_session_state
from .paths import (
    source_config_toml,
    source_credentials_dir,
    source_kimi_json,
    source_mcp_json,
    source_mcp_oauth_dir,
    source_plugins_dir,
    source_sessions_dir,
    source_user_history_dir,
)
from .types import (
    MigrationPlan,
    SessionEntry,
    SessionMigrationFailure,
    WorkDirEntry,
)
from .workdir_bucket import old_md5_bucket_name

MD5_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class DetectOptions:
    source_path: str


def _unknown_workdir_reason() -> str:
    return (
        "No local workdir mapping was found for this legacy session bucket; "
        "kimi.json may be missing, unreadable, or not list the workdir."
    )


def _unreadable_session_reason() -> str:
    return (
        "Legacy session could not be inspected because context.jsonl is "
        "missing or unreadable."
    )


def _format_error(error: BaseException) -> str:
    return str(error) or error.__class__.__name__


def _list_dir_safe(dir_path: str, filter_fn) -> list[str]:
    try:
        return [n for n in os.listdir(dir_path) if filter_fn(n)]
    except OSError:
        return []


def _read_wire_mtime(session_dir: str) -> int:
    state_path = os.path.join(session_dir, "state.json")
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            parsed = parse_session_state(f.read())
        wire_mtime = parsed.get("wire_mtime")
        if wire_mtime is not None:
            return int(wire_mtime * 1000)
    except (OSError, ValueError):
        # fall through to wire.jsonl mtime
        pass
    try:
        st = os.stat(os.path.join(session_dir, "wire.jsonl"))
        return int(st.st_mtime * 1000)
    except OSError:
        return 0


def detect_migration(opts: DetectOptions) -> MigrationPlan:
    """Scan ``opts.source_path`` for legacy kimi-cli state."""
    src = opts.source_path

    has_config = os.path.exists(source_config_toml(src))
    has_mcp = os.path.exists(source_mcp_json(src))
    has_user_history = os.path.exists(source_user_history_dir(src))

    oauth_credentials = _list_dir_safe(
        source_credentials_dir(src), lambda n: n.endswith(".json")
    )
    detected_plugins = _list_dir_safe(source_plugins_dir(src), lambda _: True)
    detected_mcp_oauth_servers = _list_dir_safe(
        source_mcp_oauth_dir(src), lambda _: True
    )

    # Reverse-lookup workdir from kimi.json
    workdir_map: dict[str, dict[str, str]] = {}
    try:
        with open(source_kimi_json(src), "r", encoding="utf-8") as f:
            parsed = parse_kimi_json(f.read())
        for wd in parsed["work_dirs"]:
            workdir_map[old_md5_bucket_name(wd["path"])] = {
                "path": wd["path"],
                "kaos": wd["kaos"],
            }
    except (OSError, ValueError):
        # no kimi.json or unparseable -- sessions list will be empty
        pass

    workdirs: list[WorkDirEntry] = []
    total_sessions = 0
    session_scan_failures: list[SessionMigrationFailure] = []

    sessions_root = source_sessions_dir(src)
    try:
        bucket_names = os.listdir(sessions_root)
    except OSError:
        # Missing sessions dir is fine; report an empty plan.
        return MigrationPlan(
            source_home=src,
            has_config=has_config,
            has_mcp=has_mcp,
            has_user_history=has_user_history,
            oauth_credentials=oauth_credentials,
            workdirs=workdirs,
            detected_plugins=detected_plugins,
            detected_mcp_oauth_servers=detected_mcp_oauth_servers,
            total_sessions=total_sessions,
            session_scan_failures=session_scan_failures,
        )

    for bucket_name in bucket_names:
        bucket_path = os.path.join(sessions_root, bucket_name)
        # Skip non-local-kaos buckets (`<kaos>_<md5>`), which cannot be
        # represented by the local kimi-code runtime. Every other unknown bucket
        # is user data we failed to map and must remain visible.
        if not MD5_HEX_RE.match(bucket_name):
            separator = bucket_name.rfind("_")
            if separator > 0 and MD5_HEX_RE.match(bucket_name[separator + 1 :]):
                continue
            session_scan_failures.append(
                SessionMigrationFailure(
                    source_path=bucket_path, reason=_unknown_workdir_reason()
                )
            )
            continue

        wd = workdir_map.get(bucket_name)
        if wd is None:
            session_scan_failures.append(
                SessionMigrationFailure(
                    source_path=bucket_path, reason=_unknown_workdir_reason()
                )
            )
            continue
        if wd["kaos"] != "local":
            continue

        try:
            uuids = os.listdir(bucket_path)
        except OSError as error:
            session_scan_failures.append(
                SessionMigrationFailure(
                    source_path=bucket_path,
                    reason=f"Legacy session bucket could not be read: "
                    f"{_format_error(error)}",
                )
            )
            continue

        sessions: list[SessionEntry] = []
        for uuid in uuids:
            session_dir = os.path.join(bucket_path, uuid)
            cls = classify_session_dir(session_dir)
            if cls == "malformed":
                session_scan_failures.append(
                    SessionMigrationFailure(
                        source_path=session_dir, reason=_unreadable_session_reason()
                    )
                )
                continue
            if cls != "real":
                continue
            wire_mtime = _read_wire_mtime(session_dir)
            sessions.append(
                SessionEntry(uuid=uuid, old_dir=session_dir, wire_mtime=wire_mtime)
            )
            total_sessions += 1

        if sessions:
            workdirs.append(
                WorkDirEntry(
                    old_hash_dir=bucket_path,
                    workdir_path=wd["path"],
                    sessions=sessions,
                )
            )

    return MigrationPlan(
        source_home=src,
        has_config=has_config,
        has_mcp=has_mcp,
        has_user_history=has_user_history,
        oauth_credentials=oauth_credentials,
        workdirs=workdirs,
        detected_plugins=detected_plugins,
        detected_mcp_oauth_servers=detected_mcp_oauth_servers,
        total_sessions=total_sessions,
        session_scan_failures=session_scan_failures,
    )
