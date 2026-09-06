# -*- coding: utf-8 -*-
"""qxt migrate CLI 端到端测试 —— detect / run / status。"""
import json
import os

from qingxiaotuan.cli.cmd_migrate import cmd_migrate
from qingxiaotuan.ports.migration_legacy.runner import run_migration
from qingxiaotuan.ports.migration_legacy.workdir_bucket import old_md5_bucket_name


def _make_legacy(src):
    workdir = "/workspace/example"
    bucket = src / "sessions" / old_md5_bucket_name(workdir)
    (bucket / "uuid-1").mkdir(parents=True)
    (bucket / "uuid-1" / "context.jsonl").write_text(
        '{"role":"user","content":"hello"}\n', encoding="utf-8"
    )
    (src / "config.toml").write_text("# legacy config\nkey = 1\n", encoding="utf-8")
    (src / "mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
    (src / "kimi.json").write_text(
        json.dumps({"work_dirs": [{"path": workdir, "kaos": "local"}]}),
        encoding="utf-8",
    )
    return src


def _args(**kw):
    """构造 argparse.Namespace 风格的参数对象。"""
    defaults = {"migrate_cmd": None, "source": None, "target": None}
    defaults.update(kw)
    return type("A", (), defaults)()


def test_migrate_detect_reports_legacy_template(tmp_path, capsys):
    src = _make_legacy(tmp_path / "legacy")
    code = cmd_migrate(_args(migrate_cmd="detect", source=str(src)))
    out = capsys.readouterr().out
    assert code == 0
    assert "配置: 有" in out
    assert "MCP: 有" in out
    assert "会话数: 1" in out
    assert "未迁移" in out


def test_migrate_detect_missing_source(tmp_path, capsys):
    code = cmd_migrate(_args(migrate_cmd="detect", source=str(tmp_path / "nope")))
    assert code == 1


def test_migrate_run_writes_target_and_marker(tmp_path, capsys):
    src = _make_legacy(tmp_path / "legacy")
    tgt = tmp_path / "new-home"
    code = cmd_migrate(_args(migrate_cmd="run", source=str(src), target=str(tgt)))
    assert code == 0
    assert (tgt / "config.toml").is_file()
    assert (tgt / "mcp.json").is_file()
    assert (tgt / "migration-report.json").is_file()
    out = capsys.readouterr().out
    # 报告输出应指向真实的 report 文件路径 (console 可能软换行, 归一化后断言)
    assert "migration-report.json" in out.replace("\n", "")
    from qingxiaotuan.ports.migration_legacy.paths import migrated_marker
    assert os.path.exists(migrated_marker(str(src)))
    # 二次 run 仍是幂等 (no-op 但成功)
    code2 = cmd_migrate(_args(migrate_cmd="run", source=str(src), target=str(tgt)))
    assert code2 == 0


def test_migrate_run_does_not_overwrite_user_modified(tmp_path):
    src = _make_legacy(tmp_path / "legacy")
    tgt = tmp_path / "new-home"
    tgt.mkdir()
    modified = "# user-modified\n"
    (tgt / "config.toml").write_text(modified, encoding="utf-8")
    cmd_migrate(_args(migrate_cmd="run", source=str(src), target=str(tgt)))
    assert (tgt / "config.toml").read_text(encoding="utf-8") == modified


def test_migrate_status_reflects_migrated(tmp_path, capsys):
    src = _make_legacy(tmp_path / "legacy")
    tgt = tmp_path / "new-home"
    assert run_migration(str(src), str(tgt)).applied is True
    code = cmd_migrate(_args(migrate_cmd="status", source=str(src)))
    out = capsys.readouterr().out
    assert code == 0
    assert "已迁移" in out


def test_migrate_status_not_migrated(tmp_path, capsys):
    src = tmp_path / "legacy"
    src.mkdir()
    code = cmd_migrate(_args(migrate_cmd="status", source=str(src)))
    out = capsys.readouterr().out
    assert code == 0
    assert "未迁移" in out


def test_migrate_unknown_subcommand_returns_2(capsys):
    code = cmd_migrate(_args(migrate_cmd=None))
    assert code == 2