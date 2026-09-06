"""Dataclasses describing the detected migration plan and related structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionEntry:
    uuid: str
    old_dir: str
    wire_mtime: int  # unix-ms, for "recent" sort; 0 if unknown


@dataclass
class SessionMigrationFailure:
    source_path: str
    reason: str


@dataclass
class WorkDirEntry:
    old_hash_dir: str  # absolute path to ~/.kimi/sessions/<md5>/
    workdir_path: str  # resolved absolute filesystem path
    sessions: list[SessionEntry] = field(default_factory=list)


@dataclass
class MigrationPlan:
    source_home: str
    has_config: bool = False
    has_mcp: bool = False
    has_user_history: bool = False
    oauth_credentials: list[str] = field(default_factory=list)
    workdirs: list[WorkDirEntry] = field(default_factory=list)
    detected_plugins: list[str] = field(default_factory=list)
    detected_mcp_oauth_servers: list[str] = field(default_factory=list)
    total_sessions: int = 0
    session_scan_failures: list[SessionMigrationFailure] = field(default_factory=list)
