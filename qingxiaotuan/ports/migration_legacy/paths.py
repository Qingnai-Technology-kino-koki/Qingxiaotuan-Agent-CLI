"""Path helpers for the legacy kimi-cli (~/.kimi/) -> kimi-code (~/.kimi-code/)
migration. Functions return absolute-ish joined paths for a given home dir.
"""

from __future__ import annotations

import os


# Source (~/.kimi/) paths
def source_credentials_dir(src: str) -> str:
    return os.path.join(src, "credentials")


def source_sessions_dir(src: str) -> str:
    return os.path.join(src, "sessions")


def source_user_history_dir(src: str) -> str:
    return os.path.join(src, "user-history")


def source_skills_dir(src: str) -> str:
    return os.path.join(src, "skills")


def source_kimi_json(src: str) -> str:
    return os.path.join(src, "kimi.json")


def source_config_toml(src: str) -> str:
    return os.path.join(src, "config.toml")


def source_mcp_json(src: str) -> str:
    return os.path.join(src, "mcp.json")


def source_mcp_oauth_dir(src: str) -> str:
    return os.path.join(src, "mcp-oauth")


def source_plugins_dir(src: str) -> str:
    return os.path.join(src, "plugins")


def migrated_marker(src: str) -> str:
    return os.path.join(src, ".migrated-to-kimi-code")


# Target (~/.kimi-code/) paths
def target_sessions_dir(tgt: str) -> str:
    return os.path.join(tgt, "sessions")


def target_user_history_dir(tgt: str) -> str:
    return os.path.join(tgt, "user-history")


def target_skills_dir(tgt: str) -> str:
    return os.path.join(tgt, "skills")


def target_config_file(tgt: str) -> str:
    return os.path.join(tgt, "config.toml")


def target_tui_file(tgt: str) -> str:
    return os.path.join(tgt, "tui.toml")


def target_mcp_file(tgt: str) -> str:
    return os.path.join(tgt, "mcp.json")


def target_session_index(tgt: str) -> str:
    return os.path.join(tgt, "session_index.jsonl")


def migration_report_file(tgt: str) -> str:
    return os.path.join(tgt, "migration-report.json")


def migration_errors_log_file(tgt: str) -> str:
    return os.path.join(tgt, "migration-errors.log")


def skip_marker(tgt: str) -> str:
    return os.path.join(tgt, ".skip-migration-from-kimi-cli")


# Sibling fallback paths used when target file conflicts with user-modified content
def sibling_config_toml(tgt: str) -> str:
    return os.path.join(tgt, "config.migrated-from-kimi-cli.toml")


def sibling_tui_toml(tgt: str) -> str:
    return os.path.join(tgt, "tui.migrated-from-kimi-cli.toml")


def sibling_mcp_json(tgt: str) -> str:
    return os.path.join(tgt, "mcp.migrated-from-kimi-cli.json")
