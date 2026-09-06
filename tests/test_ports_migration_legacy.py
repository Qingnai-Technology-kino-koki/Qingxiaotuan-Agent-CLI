"""Tests for the ported migration-legacy utilities.

All filesystem tests use pytest's ``tmp_path`` fixture -- no personal/user
directories are touched.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from qingxiaotuan.ports.migration_legacy import (
    MigrationScope,
    analyze_context_content,
    append_marker_run,
    append_session_index_entry,
    atomic_write,
    classify_session_dir,
    detect_legacy,
    ensure_session_index_entry,
    is_config_stub_or_missing,
    is_tui_stub_or_missing,
    old_md5_bucket_name,
    read_marker,
    resolve_migration_scope,
    should_suppress_migration,
    write_marker,
    write_migration_errors_log,
    write_report,
)
from qingxiaotuan.ports.migration_legacy import paths as P


# --------------------------------------------------------------------------- #
# atomic_write
# --------------------------------------------------------------------------- #
def test_atomic_write_creates_file_and_cleans_temp(tmp_path):
    target = tmp_path / "config.toml"
    atomic_write(str(target), 'api_key = "secret"\n')
    assert target.read_text(encoding="utf-8") == 'api_key = "secret"\n'
    # no leftover temp files
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits untested on Windows")
def test_atomic_write_private_permissions(tmp_path):
    target = tmp_path / "config.toml"
    atomic_write(str(target), "secret")
    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o600


def test_atomic_write_cleans_temp_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"

    def boom(*_a, **_k):
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write(str(target), "secret")
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_no_leak_when_parent_missing(tmp_path):
    target = tmp_path / "sub" / "config.toml"
    with pytest.raises(OSError):
        atomic_write(str(target), "secret")
    assert not target.exists()


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def test_source_paths():
    assert P.source_credentials_dir("/x/.kimi") == os.path.join("/x/.kimi", "credentials")
    assert P.source_sessions_dir("/x") == os.path.join("/x", "sessions")
    assert P.source_user_history_dir("/x") == os.path.join("/x", "user-history")
    assert P.source_kimi_json("/x") == os.path.join("/x", "kimi.json")
    assert P.source_config_toml("/x") == os.path.join("/x", "config.toml")
    assert P.source_mcp_json("/x") == os.path.join("/x", "mcp.json")


def test_target_paths():
    assert P.target_config_file("/y") == os.path.join("/y", "config.toml")
    assert P.target_tui_file("/y") == os.path.join("/y", "tui.toml")
    assert P.target_session_index("/y") == os.path.join("/y", "session_index.jsonl")
    assert P.migration_report_file("/y") == os.path.join("/y", "migration-report.json")
    assert P.migrated_marker("/x/.kimi") == os.path.join("/x/.kimi", ".migrated-to-kimi-code")
    assert P.skip_marker("/y/.kimi-code") == os.path.join(
        "/y/.kimi-code", ".skip-migration-from-kimi-cli"
    )


# --------------------------------------------------------------------------- #
# kimi_cli_schema
# --------------------------------------------------------------------------- #
from qingxiaotuan.ports.migration_legacy.kimi_cli_schema import (
    parse_kimi_json,
    parse_session_state,
    SchemaError,
)


def test_parse_kimi_json_real_shape():
    parsed = parse_kimi_json(
        json.dumps(
            {
                "work_dirs": [
                    {"path": "/Users/x/proj", "kaos": "local", "last_session_id": "abc"},
                    {"path": "/Users/x/other", "kaos": "local", "last_session_id": None},
                ]
            }
        )
    )
    assert len(parsed["work_dirs"]) == 2
    assert parsed["work_dirs"][0]["kaos"] == "local"


def test_parse_kimi_json_defaults():
    parsed = parse_kimi_json(json.dumps({"work_dirs": [{"path": "/x"}]}))
    assert parsed["work_dirs"][0]["kaos"] == "local"
    assert parsed["work_dirs"][0]["last_session_id"] is None


def test_parse_kimi_json_invalid():
    with pytest.raises(SchemaError):
        parse_kimi_json(json.dumps({"work_dirs": [{"kaos": "local"}]}))  # missing path


def test_parse_session_state_tolerates_missing():
    state = parse_session_state(json.dumps({"version": 1}))
    assert state.get("version") == 1


def test_parse_session_state_passthrough():
    state = parse_session_state(
        json.dumps({"version": 1, "wire_mtime": 1772616338.93, "extra": "kept"})
    )
    assert state["wire_mtime"] == 1772616338.93
    assert state["extra"] == "kept"


# --------------------------------------------------------------------------- #
# marker
# --------------------------------------------------------------------------- #
def test_read_marker_missing(tmp_path):
    assert read_marker(str(tmp_path)) is None


def test_marker_round_trip(tmp_path):
    run = type(
        "R",
        (),
        {
            "started_at": "2026-05-16T10:00:00Z",
            "completed_at": "2026-05-16T10:00:42Z",
            "migrator_version": "0.1.1",
            "summary": {"sessionsAttempted": 5},
        },
    )()
    write_marker(str(tmp_path), run, target_path="/foo")  # type: ignore[arg-type]
    data = read_marker(str(tmp_path))
    assert data is not None
    assert data.first_migrated_at == "2026-05-16T10:00:00Z"
    assert data.last_migrated_at == "2026-05-16T10:00:42Z"
    assert data.target_paths == ["/foo"]
    assert len(data.runs) == 1


def test_append_marker_run(tmp_path):
    r1 = type(
        "R",
        (),
        {
            "started_at": "2026-05-16T10:00:00Z",
            "completed_at": "2026-05-16T10:00:42Z",
            "migrator_version": "0.1.1",
            "summary": {},
        },
    )()
    write_marker(str(tmp_path), r1, target_path="/foo")  # type: ignore[arg-type]
    r2 = type(
        "R",
        (),
        {
            "started_at": "2026-05-17T10:00:00Z",
            "completed_at": "2026-05-17T10:00:30Z",
            "migrator_version": "0.2.0",
            "summary": {},
        },
    )()
    append_marker_run(str(tmp_path), r2, target_path="/bar")  # type: ignore[arg-type]
    data = read_marker(str(tmp_path))
    assert data.first_migrated_at == "2026-05-16T10:00:00Z"
    assert data.last_migrated_at == "2026-05-17T10:00:30Z"
    assert len(data.runs) == 2
    assert data.target_path == "/bar"


def test_read_marker_corrupt(tmp_path):
    (tmp_path / ".migrated-to-kimi-code").write_text("not-json", encoding="utf-8")
    assert read_marker(str(tmp_path)) is None


def test_read_marker_missing_runs(tmp_path):
    (tmp_path / ".migrated-to-kimi-code").write_text(
        json.dumps({"version": 1, "target_path": "/foo"}), encoding="utf-8"
    )
    assert read_marker(str(tmp_path)) is None


def test_should_suppress_no_marker(tmp_path):
    assert (
        should_suppress_migration(
            type("I", (), {"source_home": str(tmp_path), "target_home": str(tmp_path / "target")})()
        )
        is False
    )


def test_should_suppress_same_target(tmp_path):
    target = tmp_path / "target"
    (tmp_path / ".migrated-to-kimi-code").write_text(
        json.dumps({"target_path": str(target)}), encoding="utf-8"
    )
    assert (
        should_suppress_migration(
            type("I", (), {"source_home": str(tmp_path), "target_home": str(target)})()
        )
        is True
    )


def test_should_suppress_different_target(tmp_path):
    (tmp_path / ".migrated-to-kimi-code").write_text(
        json.dumps({"target_path": str(tmp_path / "first")}), encoding="utf-8"
    )
    assert (
        should_suppress_migration(
            type("I", (), {"source_home": str(tmp_path), "target_home": str(tmp_path / "second")})()
        )
        is False
    )


def test_should_suppress_legacy_marker_no_target(tmp_path):
    (tmp_path / ".migrated-to-kimi-code").write_text(json.dumps({}), encoding="utf-8")
    assert (
        should_suppress_migration(
            type("I", (), {"source_home": str(tmp_path), "target_home": str(tmp_path / "target")})()
        )
        is True
    )


def test_should_suppress_corrupt_marker(tmp_path):
    (tmp_path / ".migrated-to-kimi-code").write_text("not-json", encoding="utf-8")
    assert (
        should_suppress_migration(
            type("I", (), {"source_home": str(tmp_path), "target_home": str(tmp_path / "target")})()
        )
        is True
    )


def test_should_suppress_skip_marker(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / ".skip-migration-from-kimi-cli").write_text("", encoding="utf-8")
    assert (
        should_suppress_migration(
            type("I", (), {"source_home": str(tmp_path), "target_home": str(target)})()
        )
        is True
    )


def test_should_suppress_windows_drive_case(tmp_path):
    (tmp_path / ".migrated-to-kimi-code").write_text(
        json.dumps({"target_path": "C:\\Users\\Example\\.kimi-code"}), encoding="utf-8"
    )
    assert (
        should_suppress_migration(
            type(
                "I",
                (),
                {
                    "source_home": str(tmp_path),
                    "target_home": "c:\\Users\\Example\\.kimi-code",
                },
            )()
        )
        is True
    )


# --------------------------------------------------------------------------- #
# migration_errors_log
# --------------------------------------------------------------------------- #
def test_errors_log_no_failures(tmp_path):
    write_migration_errors_log(
        str(tmp_path),
        type("I", (), {"started_at": "2026-05-19T00:00:00Z", "failures": []})(),
    )
    log = (tmp_path / "migration-errors.log").read_text(encoding="utf-8")
    assert "===== migration run @ 2026-05-19T00:00:00Z =====" in log
    assert "no failures." in log


def test_errors_log_with_failures_and_histogram(tmp_path, tmp_path_factory):
    src = tmp_path_factory.mktemp("src")
    session_dir = src / "ses-1"
    session_dir.mkdir()
    (session_dir / "context.jsonl").write_text(
        '{"role":"_system_prompt","content":"x"}\n{"role":"user","content":"hi"}\n',
        encoding="utf-8",
    )
    write_migration_errors_log(
        str(tmp_path),
        type(
            "I",
            (),
            {
                "started_at": "2026-05-19T00:00:00Z",
                "failures": [
                    type("F", (), {"source_path": str(session_dir), "reason": "ENOSPC"})()
                ],
            },
        )(),
    )
    log = (tmp_path / "migration-errors.log").read_text(encoding="utf-8")
    assert "1 session(s) failed to migrate." in log
    assert str(session_dir) in log
    assert "ENOSPC" in log
    assert "context.jsonl: 2 lines" in log
    assert "_system_prompt=1" in log
    assert "user=1" in log


def test_errors_log_append_only(tmp_path, tmp_path_factory):
    src = tmp_path_factory.mktemp("src")
    s1 = src / "ses-1"
    s2 = src / "ses-2"
    s1.mkdir()
    s2.mkdir()
    (s1 / "context.jsonl").write_text('{"role":"user","content":"a"}\n', encoding="utf-8")
    (s2 / "context.jsonl").write_text('{"role":"user","content":"b"}\n', encoding="utf-8")

    write_migration_errors_log(
        str(tmp_path),
        type(
            "I",
            (),
            {
                "started_at": "2026-05-19T00:00:00Z",
                "failures": [
                    type("F", (), {"source_path": str(s1), "reason": "first-run"})()
                ],
            },
        )(),
    )
    write_migration_errors_log(
        str(tmp_path),
        type(
            "I",
            (),
            {
                "started_at": "2026-05-20T00:00:00Z",
                "failures": [
                    type("F", (), {"source_path": str(s2), "reason": "second-run"})()
                ],
            },
        )(),
    )
    log = (tmp_path / "migration-errors.log").read_text(encoding="utf-8")
    assert log.count("===== migration run @ ") == 2
    assert "first-run" in log
    assert "second-run" in log


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
def test_resolve_config_only():
    res = resolve_migration_scope(["now", "config-only"])
    assert res.decision == "now"
    assert res.scope == MigrationScope(
        config=True, mcp=True, user_history=True, skills=True, sessions=False
    )


def test_resolve_all_sessions():
    res = resolve_migration_scope(["now", "all-sessions"])
    assert res.decision == "now"
    assert res.scope.sessions is True


def test_resolve_later():
    res = resolve_migration_scope(["later"])
    assert res.decision == "later"
    assert res.scope is None


def test_resolve_never():
    res = resolve_migration_scope(["never"])
    assert res.decision == "never"
    assert res.scope is None


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def test_write_report(tmp_path):
    report = {
        "startedAt": "s",
        "completedAt": "e",
        "migratorVersion": "0.1.1",
        "source": "/x",
        "target": str(tmp_path),
        "summary": {},
        "notices": {},
    }
    write_report(str(tmp_path), report)
    text = (tmp_path / "migration-report.json").read_text(encoding="utf-8")
    assert json.loads(text)["migratorVersion"] == "0.1.1"


# --------------------------------------------------------------------------- #
# session_index
# --------------------------------------------------------------------------- #
def test_session_index_append_and_ensure(tmp_path):
    from qingxiaotuan.ports.migration_legacy import SessionIndexEntry

    e1 = SessionIndexEntry(session_id="a", session_dir="/abs/a", work_dir="/wd")
    e2 = SessionIndexEntry(session_id="b", session_dir="/abs/b", work_dir="/wd")
    append_session_index_entry(str(tmp_path), e1)
    append_session_index_entry(str(tmp_path), e2)
    lines = [
        ln for ln in (tmp_path / "session_index.jsonl").read_text(encoding="utf-8").split("\n") if ln
    ]
    assert len(lines) == 2

    # ensure is a no-op for an existing id
    ensure_session_index_entry(str(tmp_path), e1)
    lines = [
        ln for ln in (tmp_path / "session_index.jsonl").read_text(encoding="utf-8").split("\n") if ln
    ]
    assert len(lines) == 2

    # ensure appends a missing id
    ensure_session_index_entry(
        str(tmp_path),
        SessionIndexEntry(session_id="c", session_dir="/abs/c", work_dir="/wd"),
    )
    lines = [
        ln for ln in (tmp_path / "session_index.jsonl").read_text(encoding="utf-8").split("\n") if ln
    ]
    assert len(lines) == 3


# --------------------------------------------------------------------------- #
# stub_detect
# --------------------------------------------------------------------------- #
def test_is_config_stub_or_missing(tmp_path):
    from qingxiaotuan.ports.migration_legacy import DEFAULT_CONFIG_FILE_TEXT

    assert is_config_stub_or_missing(str(tmp_path / "missing.toml")) is True
    (tmp_path / "config.toml").write_text(DEFAULT_CONFIG_FILE_TEXT, encoding="utf-8")
    assert is_config_stub_or_missing(str(tmp_path / "config.toml")) is True
    (tmp_path / "modified.toml").write_text(
        DEFAULT_CONFIG_FILE_TEXT + "default_thinking = true\n", encoding="utf-8"
    )
    assert is_config_stub_or_missing(str(tmp_path / "modified.toml")) is False


def test_is_tui_stub_or_missing(tmp_path):
    from qingxiaotuan.ports.migration_legacy import DEFAULT_TUI_RENDER

    assert is_tui_stub_or_missing(str(tmp_path / "missing.toml")) is True
    (tmp_path / "tui.toml").write_text(DEFAULT_TUI_RENDER, encoding="utf-8")
    assert is_tui_stub_or_missing(str(tmp_path / "tui.toml")) is True
    # semantically-equal reformat is still a stub
    (tmp_path / "reformatted.toml").write_text(
        'theme = "auto"\n[editor]\ncommand = ""\n[notifications]\n'
        'enabled = true\nnotification_condition = "unfocused"\n',
        encoding="utf-8",
    )
    assert is_tui_stub_or_missing(str(tmp_path / "reformatted.toml")) is True
    # changed theme -> user-modified
    (tmp_path / "dark.toml").write_text(
        'theme = "dark"\n[editor]\ncommand = ""\n[notifications]\n'
        'enabled = true\nnotification_condition = "unfocused"\n',
        encoding="utf-8",
    )
    assert is_tui_stub_or_missing(str(tmp_path / "dark.toml")) is False


# --------------------------------------------------------------------------- #
# classify + context
# --------------------------------------------------------------------------- #
def test_analyze_context_content():
    assert analyze_context_content([]) == "empty"
    assert analyze_context_content([""]) == "empty"
    assert (
        analyze_context_content(['{"role":"_system_prompt","content":"x"}']) == "empty"
    )
    assert (
        analyze_context_content(['{"role":"user","content":"hi"}']) == "real"
    )
    assert analyze_context_content(["not-json\n{broken\n"]) == "corrupt"


def test_classify_session_dir(tmp_path):
    def make(name, files):
        d = tmp_path / name
        d.mkdir()
        for k, v in files.items():
            (d / k).write_text(v, encoding="utf-8")
        return str(d)

    assert classify_session_dir(make("placeholder", {"test": "x"})) == "placeholder"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert classify_session_dir(str(empty)) == "empty"
    assert classify_session_dir(make("malformed", {"wire.jsonl": "{}\n"})) == "malformed"
    assert (
        classify_session_dir(
            make(
                "real",
                {
                    "state.json": "{}",
                    "context.jsonl": '{"role":"user","content":"hi"}\n',
                },
            )
        )
        == "real"
    )
    assert (
        classify_session_dir(make("corrupt", {"context.jsonl": "not-json\n"})) == "real"
    )


# --------------------------------------------------------------------------- #
# workdir_bucket + detect
# --------------------------------------------------------------------------- #
def test_old_md5_bucket_name():
    import hashlib

    assert old_md5_bucket_name("/workspace/example") == hashlib.md5(
        "/workspace/example".encode("utf-8")
    ).hexdigest()


def test_detect_empty(tmp_path):
    plan = detect_legacy(type("O", (), {"source_path": str(tmp_path)})())
    assert plan.has_config is False
    assert plan.has_mcp is False
    assert plan.total_sessions == 0


def test_detect_presence(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
    creds = tmp_path / "credentials"
    creds.mkdir()
    (creds / "kimi-code.json").write_text("{}", encoding="utf-8")
    (tmp_path / "user-history").mkdir()
    plugins = tmp_path / "plugins" / "p1"
    plugins.mkdir(parents=True)
    mcp_oauth = tmp_path / "mcp-oauth"
    mcp_oauth.mkdir()
    (mcp_oauth / "server-1").write_text("", encoding="utf-8")

    plan = detect_legacy(type("O", (), {"source_path": str(tmp_path)})())
    assert plan.has_config is True
    assert plan.has_mcp is True
    assert plan.has_user_history is True
    assert plan.oauth_credentials == ["kimi-code.json"]
    assert plan.detected_plugins == ["p1"]
    assert "server-1" in plan.detected_mcp_oauth_servers


def test_detect_unknown_workdir_bucket(tmp_path):
    bucket = tmp_path / "sessions" / old_md5_bucket_name("/workspace/example")
    (bucket / "legacy-session").mkdir(parents=True)
    (bucket / "legacy-session" / "context.jsonl").write_text(
        '{"role":"user","content":"hello"}\n', encoding="utf-8"
    )
    plan = detect_legacy(type("O", (), {"source_path": str(tmp_path)})())
    assert plan.total_sessions == 0
    assert len(plan.session_scan_failures) == 1
    assert "kimi.json" in plan.session_scan_failures[0].reason


def test_detect_real_session(tmp_path):
    workdir = "/workspace/example"
    bucket = tmp_path / "sessions" / old_md5_bucket_name(workdir)
    (bucket / "uuid-1").mkdir(parents=True)
    (bucket / "uuid-1" / "context.jsonl").write_text(
        '{"role":"user","content":"hello"}\n', encoding="utf-8"
    )
    (tmp_path / "kimi.json").write_text(
        json.dumps({"work_dirs": [{"path": workdir, "kaos": "local"}]}),
        encoding="utf-8",
    )
    plan = detect_legacy(type("O", (), {"source_path": str(tmp_path)})())
    assert plan.total_sessions == 1
    assert len(plan.workdirs) == 1
    assert plan.workdirs[0].sessions[0].uuid == "uuid-1"
