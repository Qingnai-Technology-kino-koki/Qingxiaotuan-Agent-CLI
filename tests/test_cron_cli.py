"""Cron CLI + 守护 + 精确引用跳转 (qxt open) 的离线测试。"""
import os
import sys
from pathlib import Path

import pytest

from qingxiaotuan.cron.store import CronStore
from qingxiaotuan.cli.commands import cmd_cron, cmd_open
from qingxiaotuan.cli.parser import build_parser


# ------------------------------------------------------------------ CronStore CRUD

def test_store_crud(tmp_path):
    store = CronStore(tmp_path)
    job = store.add("日报", "写日报", interval_minutes=30)
    assert job["id"]
    assert job["enabled"] is True
    jobs = store.list()
    assert len(jobs) == 1
    assert jobs[0]["name"] == "日报"
    # 新任务 last_run=0, 视为到期
    assert len(store.due()) == 1
    # mark_run 后不再到期
    store.mark_run(job["id"])
    assert store.due() == []
    # 删除
    assert store.remove(job["id"]) is True
    assert store.remove(job["id"]) is False
    assert store.list() == []


def test_store_due_respects_interval(tmp_path):
    store = CronStore(tmp_path)
    job = store.add("快任务", "x", interval_minutes=0)
    jobs = store._read()
    for j in jobs:
        if j["id"] == job["id"]:
            j["last_run"] = 0
    store._write(jobs)
    assert len(store.due()) == 1


# ------------------------------------------------------------------ cmd_cron (CLI 层)

def _run_cron(tmp_path, argv):
    parser = build_parser()
    args = parser.parse_args(["cron"] + argv)
    old_home = os.environ.get("QXT_HOME")
    os.environ["QXT_HOME"] = str(tmp_path)
    try:
        return cmd_cron(args)
    finally:
        if old_home is None:
            os.environ.pop("QXT_HOME", None)
        else:
            os.environ["QXT_HOME"] = old_home


def test_cmd_cron_add_list_remove(tmp_path):
    assert _run_cron(tmp_path, ["add", "测试任务", "hello", "--interval", "5"]) == 0
    assert _run_cron(tmp_path, ["list"]) == 0
    store = CronStore(tmp_path)
    jobs = store.list()
    assert len(jobs) == 1
    assert jobs[0]["interval_minutes"] == 5
    assert _run_cron(tmp_path, ["remove", jobs[0]["id"]]) == 0
    assert store.list() == []


def test_cmd_cron_enable_disable_edit(tmp_path):
    assert _run_cron(tmp_path, ["add", "任务A", "promptA", "--interval", "10"]) == 0
    store = CronStore(tmp_path)
    jid = store.list()[0]["id"]
    # disable → 不再到期
    assert _run_cron(tmp_path, ["disable", jid]) == 0
    assert store.get(jid)["enabled"] is False
    assert store.due() == []
    # enable → 恢复
    assert _run_cron(tmp_path, ["enable", jid]) == 0
    assert store.get(jid)["enabled"] is True
    # edit 间隔与提示词
    assert _run_cron(tmp_path, ["edit", jid, "--interval", "30", "--prompt", "新提示"]) == 0
    job = store.get(jid)
    assert job["interval_minutes"] == 30
    assert job["prompt"] == "新提示"
    # 未找到
    assert _run_cron(tmp_path, ["disable", "nope"]) == 1
    assert _run_cron(tmp_path, ["edit", "nope", "--interval", "5"]) == 1


def test_cmd_cron_add_output(tmp_path):
    out_file = tmp_path / "out" / "result.md"
    assert _run_cron(tmp_path, ["add", "落盘任务", "prompt", "--interval", "5",
                                "--output", str(out_file)]) == 0
    store = CronStore(tmp_path)
    job = store.list()[0]
    assert job["output"] == str(out_file)


def test_cmd_cron_edit_output_change_and_clear(tmp_path):
    out1 = tmp_path / "a.md"
    out2 = tmp_path / "b.md"
    assert _run_cron(tmp_path, ["add", "任务", "prompt", "--interval", "5",
                                "--output", str(out1)]) == 0
    store = CronStore(tmp_path)
    jid = store.list()[0]["id"]
    # 改输出文件
    assert _run_cron(tmp_path, ["edit", jid, "--output", str(out2)]) == 0
    assert store.get(jid)["output"] == str(out2)
    # 清除输出文件
    assert _run_cron(tmp_path, ["edit", jid, "--output", ""]) == 0
    assert store.get(jid)["output"] == ""


def test_cmd_cron_logs_no_history(tmp_path):
    assert _run_cron(tmp_path, ["add", "任务B", "promptB", "--interval", "10"]) == 0
    store = CronStore(tmp_path)
    jid = store.list()[0]["id"]
    assert _run_cron(tmp_path, ["logs", jid]) == 0
    assert _run_cron(tmp_path, ["logs", "nope"]) == 1


def test_cmd_cron_tick_no_jobs(tmp_path):
    assert _run_cron(tmp_path, ["tick"]) == 0


def test_cmd_cron_status(tmp_path):
    assert _run_cron(tmp_path, ["status"]) == 0


# ------------------------------------------------------------------ 守护进程模块

def test_daemon_main_parses_args(tmp_path):
    from qingxiaotuan.cron import daemon
    assert callable(daemon.main)
    assert callable(daemon._handle_signal)


def test_daemon_heartbeat_path(tmp_path):
    from qingxiaotuan.cron.daemon import _heartbeat_path
    assert _heartbeat_path(tmp_path) == tmp_path / "cron" / "daemon.heartbeat"


# ------------------------------------------------------------------ qxt open (精确引用跳转)

def test_cmd_open_parses_file_line(tmp_path, monkeypatch):
    f = tmp_path / "demo.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    opened = {}
    import qingxiaotuan.tools.code as code_mod
    monkeypatch.setattr(code_mod, "_open_in_editor",
                        lambda p, ln, editor="": opened.update(path=str(p), line=ln) or "opened")
    parser = build_parser()
    args = parser.parse_args(["open", f"{f}:2"])
    assert args.target == f"{f}:2"
    assert cmd_open(args) == 0
    assert opened["line"] == 2
    assert opened["path"].endswith("demo.py")


def test_cmd_open_missing_file(tmp_path):
    parser = build_parser()
    args = parser.parse_args(["open", "nonexistent.py:3"])
    assert cmd_open(args) == 1


def test_cmd_open_bad_line(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x\n", encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(["open", f"{f}:abc"])
    assert cmd_open(args) == 1
