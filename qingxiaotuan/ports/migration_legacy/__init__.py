"""自研实现 (对齐上游 legacy 迁移工具; 纯 Python stdlib)。

Public API surface for the migration tooling. Only pure, dependency-free logic
is ported here; node-only orchestration/step modules are documented in
``SKIPPED.md``.
"""

from __future__ import annotations

from .atomic_write import atomic_write
from .classify import (
    analyze_context_content,
    classify_session_dir,
)
from .detect import DetectOptions, detect_migration
from .marker import (
    MarkerRun,
    MarkerData,
    MigrationSuppressionInput,
    append_marker_run,
    read_marker,
    should_suppress_migration,
    write_marker,
)
from .migration_errors_log import (
    MigrationFailureEntry,
    MigrationErrorsLogInput,
    write_migration_errors_log,
)
from .paths import (  # path helpers
    migrated_marker,
    migration_errors_log_file,
    migration_report_file,
    sibling_config_toml,
    sibling_mcp_json,
    sibling_tui_toml,
    skip_marker,
    source_config_toml,
    source_credentials_dir,
    source_kimi_json,
    source_mcp_json,
    source_mcp_oauth_dir,
    source_plugins_dir,
    source_sessions_dir,
    source_skills_dir,
    source_user_history_dir,
    target_config_file,
    target_mcp_file,
    target_session_index,
    target_sessions_dir,
    target_skills_dir,
    target_tui_file,
    target_user_history_dir,
)
from .prompt import (
    MigrationPromptResult,
    MigrationScope,
    resolve_migration_scope,
)
from .report import write_report
from .runner import MigrationResult, run_migration
from .session_index import (
    SessionIndexEntry,
    append_session_index_entry,
    ensure_session_index_entry,
)
from .stub_detect import (
    DEFAULT_CONFIG_FILE_TEXT,
    DEFAULT_TUI_RENDER,
    is_config_stub_or_missing,
    is_tui_stub_or_missing,
)
from .types import (
    MigrationPlan,
    SessionEntry,
    SessionMigrationFailure,
    WorkDirEntry,
)
from .workdir_bucket import old_md5_bucket_name

# Friendlier alias for the detection entry point.
detect_legacy = detect_migration

__all__ = [
    "atomic_write",
    "detect_migration",
    "detect_legacy",
    "DetectOptions",
    # runner (端到端编排)
    "run_migration",
    "MigrationResult",
    # marker
    "MarkerRun",
    "MarkerData",
    "MigrationSuppressionInput",
    "read_marker",
    "write_marker",
    "append_marker_run",
    "should_suppress_migration",
    # errors log
    "MigrationFailureEntry",
    "MigrationErrorsLogInput",
    "write_migration_errors_log",
    # paths
    "migrated_marker",
    "migration_errors_log_file",
    "migration_report_file",
    "sibling_config_toml",
    "sibling_mcp_json",
    "sibling_tui_toml",
    "skip_marker",
    "source_config_toml",
    "source_credentials_dir",
    "source_kimi_json",
    "source_mcp_json",
    "source_mcp_oauth_dir",
    "source_plugins_dir",
    "source_sessions_dir",
    "source_skills_dir",
    "source_user_history_dir",
    "target_config_file",
    "target_mcp_file",
    "target_session_index",
    "target_sessions_dir",
    "target_skills_dir",
    "target_tui_file",
    "target_user_history_dir",
    # prompt
    "MigrationPromptResult",
    "MigrationScope",
    "resolve_migration_scope",
    # report
    "write_report",
    # session index
    "SessionIndexEntry",
    "append_session_index_entry",
    "ensure_session_index_entry",
    # stub detect
    "DEFAULT_CONFIG_FILE_TEXT",
    "DEFAULT_TUI_RENDER",
    "is_config_stub_or_missing",
    "is_tui_stub_or_missing",
    # classify / context
    "analyze_context_content",
    "classify_session_dir",
    # types
    "MigrationPlan",
    "SessionEntry",
    "SessionMigrationFailure",
    "WorkDirEntry",
    # workdir bucket
    "old_md5_bucket_name",
]
