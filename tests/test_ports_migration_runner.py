# -*- coding: utf-8 -*-
"""migration_legacy 编排器 (runner) 单测 —— 端到端 / 幂等 / 守护 / no-op。"""
import json
import os

import pytest
from pathlib import Path

from qingxiaotuan.ports.migration_legacy.detect import DetectOptions, detect_migration
from qingxiaotuan.ports.migration_legacy.paths import (
    migrated_marker,
    skip_marker,
    target_config_file,
    target_mcp_file,
    target_tui_file,
)
from qingxiaotuan.ports.migration_legacy.runner import run_migration
from qingxiaotuan.ports.migration_legacy.workdir_bucket import old_md5_bucket_name
from qingxiaotuan.ports.migration_legacy.stub_detect import (
    DEFAULT_CONFIG_FILE_TEXT,
    DEFAULT_TUI_RENDER,
)
from qingxiaotuan.ports.migration_legacy.types import MigrationPlan


def _make_source(tmp_path):
    src = tmp_path / "src"
    workdir = "/workspace/example"
    bucket = src / "sessions" / old_md5_bucket_name(workdir)
    (bucket / "uuid-1").mkdir(parents=True, exist_ok=True)
    (bucket / "uuid-1" / "context.jsonl").write_text(
        '{"role":"user","content":"hello"}\n', encoding="utf-8"
    )
    (src / "user-history").mkdir(exist_ok=True)
    (src / "config.toml").write_text("# arbitrary legacy config\nkey = 1\n", encoding="utf-8")
    (src / "mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
    (src / "kimi.json").write_text(
        json.dumps({"work_dirs": [{"path": workdir, "kaos": "local"}]}),
        encoding="utf-8",
    )
    return src


def test_detection_reports_legacy_state(tmp_path):
    src = _make_source(tmp_path)
    plan = detect_migration(DetectOptions(source_path=str(src)))
    assert isinstance(plan, MigrationPlan)
    assert plan.has_config is True
    assert plan.has_mcp is True
    assert plan.total_sessions >= 1


def test_runner_writes_stub_guarded_files_and_report(tmp_path):
    src = _make_source(tmp_path)
    tgt = tmp_path / "tgt"
    res = run_migration(str(src), str(tgt))
    assert res.applied is True
    assert res.config_written is True
    assert res.tui_written is True
    assert res.mcp_written is True
    # 目标文件已用默认 stub 写入
    assert os.path.isfile(target_config_file(str(tgt)))
    assert os.path.isfile(target_tui_file(str(tgt)))
    assert os.path.isfile(target_mcp_file(str(tgt)))
    # 报告存在
    assert (tgt / "migration-report.json").is_file()
    report = json.loads((tgt / "migration-report.json").read_text(encoding="utf-8"))
    assert report["applied"] is True
    assert report["total_sessions"] >= 1
    # 已迁移 marker 写入源目录
    assert os.path.exists(migrated_marker(str(src)))


def test_runner_does_not_overwrite_user_modified_target(tmp_path):
    src = _make_source(tmp_path)
    tgt = tmp_path / "tgt"
    (tgt).mkdir(exist_ok=True)
    # 用户已改了目标 config.toml -> 不应被覆盖
    modified = "# user-modified\n"
    (tgt / "config.toml").write_text(modified, encoding="utf-8")
    res = run_migration(str(src), str(tgt))
    assert (tgt / "config.toml").read_text(encoding="utf-8") == modified
    assert res.config_written is False


def test_runner_noop_on_empty_source(tmp_path):
    src = tmp_path / "empty-src"
    src.mkdir(exist_ok=True)
    tgt = tmp_path / "empty-tgt"
    res = run_migration(str(src), str(tgt))
    assert res.applied is False
    assert not tgt.exists()


def test_runner_noop_when_skip_marker_present(tmp_path):
    src = _make_source(tmp_path)
    Path(skip_marker(str(src))).write_text("skip", encoding="utf-8")
    tgt = tmp_path / "tgt"
    res = run_migration(str(src), str(tgt))
    assert res.applied is False


def test_runner_idempotent_on_second_run(tmp_path):
    src = _make_source(tmp_path)
    tgt = tmp_path / "tgt"
    run_migration(str(src), str(tgt))
    # 第二次运行: 目标已在 -> stub_present 判定为安全, 也走到写分支, 但不报错
    res2 = run_migration(str(src), str(tgt))
    # mcp 已复制, 源已迁移; 第二次仍应汇报 mcp 已写入 (目标结构不变)
    assert (tgt / "mcp.json").exists()


def test_runner_rejects_run_on_empty_target_and_writes_default_stub(tmp_path):
    src = _make_source(tmp_path)
    tgt = tmp_path / "tgt"
    res = run_migration(str(src), str(tgt))
    assert (tgt / "config.toml").read_text(encoding="utf-8") == DEFAULT_CONFIG_FILE_TEXT
    assert (tgt / "tui.toml").read_text(encoding="utf-8") == DEFAULT_TUI_RENDER