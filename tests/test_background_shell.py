"""后台 Shell (`! cmd`) 测试 —— 异步执行 + status/logs/wait/cancel。"""

from __future__ import annotations

import time

import pytest

from qingxiaotuan.core.background_shell import (
    BackgroundShellManager, is_bg_command, run_in_background,
    strip_bg_prefix,
)


# ---------------------------------------------------------------- 前缀解析

def test_detect_bg_prefix():
    assert is_bg_command("! npm install")
    assert is_bg_command("  ! ls")
    assert not is_bg_command("echo hi")
    assert not is_bg_command("")


def test_strip_bg_prefix():
    assert strip_bg_prefix("! ls -la").strip() == "ls -la"
    assert strip_bg_prefix("  !pwd").strip() == "pwd"
    assert strip_bg_prefix("echo x").strip() == "echo x"


# ---------------------------------------------------------------- 生命周期

def test_background_job_lifecycle(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    job = mgr.start(
        'python -c "import time;print(1,flush=True);time.sleep(0.4);print(2,flush=True)"')
    assert job.job_id.startswith("shl-")
    # 立即返回 (不阻塞): 状态是 queued 或 running
    assert job.status in ("queued", "running")
    done = mgr.wait(job.job_id, timeout=10)
    assert done.status == "done"
    assert done.returncode == 0
    out = done.tail(10)
    assert "1" in out and "2" in out


def test_status_dict_and_tail(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    job = mgr.start('python -c "print(42,flush=True)"')
    mgr.wait(job.job_id, timeout=10)
    view2 = mgr.status_dict(job.job_id)
    assert view2["returncode"] == 0
    assert "42" in mgr.tail(job.job_id, 5)


def test_cancel_terminates(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    job = mgr.start('python -c "import time;time.sleep(30)"')
    time.sleep(0.6)  # 等进程起来
    assert mgr.cancel(job.job_id) is True
    done = mgr.wait(job.job_id, timeout=5)
    assert done.status == "cancelled"


def test_rehydrate_from_disk(tmp_path, qxt_home):
    """跨进程读: 新 manager 能重建磁盘上已结束的 job。"""
    mgr1 = BackgroundShellManager(tmp_path)
    job = mgr1.start('python -c "print(77,flush=True)"')
    mgr1.wait(job.job_id, timeout=10)
    # 新 manager (模拟另一个进程) 读取同一目录
    mgr2 = BackgroundShellManager(tmp_path)
    rebuilt = mgr2.get(job.job_id)
    assert rebuilt is not None
    assert rebuilt.status == "done"
    assert "77" in rebuilt.tail(5)


def test_list_and_prune(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    j1 = mgr.start('python -c "print(1,flush=True)"')
    j2 = mgr.start('python -c "print(2,flush=True)"')
    mgr.wait(j1.job_id, timeout=10)
    mgr.wait(j2.job_id, timeout=10)
    ids = [j.job_id for j in mgr.list()]
    assert j1.job_id in ids and j2.job_id in ids
    assert mgr.prune_finished() >= 2


def test_unknown_status_and_timeout(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    view = mgr.status_dict("nope")
    assert view["ok"] is False
    with pytest.raises(KeyError):
        mgr.wait("nope", timeout=1)


# ---------------------------------------------------------------- 工具层

def test_run_shell_bg_dispatches(tmp_path, qxt_home):
    import re
    from qingxiaotuan.tools.base import ToolContext
    from qingxiaotuan.tools.shell import run_bg_shell, run_shell
    ctx = ToolContext(kernel=None, workspace=str(tmp_path))
    # shell 工具遇到 `!` 前缀应立即返回 job id (而非前台阻塞)
    out = run_shell(ctx, '! python -c "print(\'bg-ok\',flush=True)"')
    assert "shl-" in out
    assert "后台" in out
    jid = re.search(r"job=(\S+)", out).group(1)
    wait_out = run_bg_shell(ctx, action="wait", job_id=jid, timeout=10, lines=5)
    assert "已结束" in wait_out
    logs = run_bg_shell(ctx, action="logs", job_id=jid, lines=5)
    assert "bg-ok" in logs


def test_run_bg_shell_list_and_errors(tmp_path, qxt_home):
    from qingxiaotuan.tools.base import ToolContext
    from qingxiaotuan.tools.shell import run_bg_shell
    ctx = ToolContext(kernel=None, workspace=str(tmp_path))
    view = run_bg_shell(ctx, action="list")
    assert "共" in view or "暂无" in view
    st = run_bg_shell(ctx, action="status")
    assert "job_id" in st  # 缺 job_id 时的提示
    err = run_bg_shell(ctx, action="status", job_id="no-such-job")
    assert "未找到" in err
    bad = run_bg_shell(ctx, action="nope", job_id="x")
    assert "未知 action" in bad


# ------------------------------------------------- Round 2: 增量跟随/超时/安全

def test_incremental_offset_tail(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    job = mgr.start('python -c "import time;print(1,flush=True);time.sleep(0.5);print(2,flush=True);print(3,flush=True)"')
    # 等第一行产出
    deadline = time.time() + 5
    while "1" not in job.live_lines and time.time() < deadline:
        time.sleep(0.05)
    v1 = mgr.tail_since(job.job_id, offset=0, limit=10)
    assert v1["ok"] and "1" in v1["text"]
    first = v1["next_offset"]
    done = mgr.wait(job.job_id, timeout=10)
    assert done.status == "done"
    v2 = mgr.tail_since(job.job_id, offset=first, limit=10)
    assert "2" in v2["text"] and "3" in v2["text"]
    assert "1" not in v2["text"]  # 只读新产出


def test_timeout_auto_kills(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    job = mgr.start('python -c "import time;time.sleep(30)"', timeout=0.8)
    done = mgr.wait(job.job_id, timeout=10)
    assert done.status == "failed"
    assert done.error and "超时" in done.error


def test_bg_command_respects_redline(tmp_path, qxt_home):
    from qingxiaotuan.tools.base import ToolContext
    from qingxiaotuan.tools.shell import run_shell
    ctx = ToolContext(kernel=None, workspace=str(tmp_path))
    out = run_shell(ctx, "! rm -rf some/dir")  # 后台也不豁免红线
    assert "已拦截" in out
    assert "shl-" not in out


def test_bg_shell_cli_parser():
    from qingxiaotuan.cli.parser import build_parser
    p = build_parser()
    a = p.parse_args(["bg", "shell", "list"])
    assert a.func == "cmd_bg" and a.bg_cmd == "shell" and a.action == "list"
    b = p.parse_args(["bg", "shell", "logs", "shl-123", "--tail", "5", "--offset", "3"])
    assert b.action == "logs" and b.job_id == "shl-123"
    assert b.tail == 5 and b.offset == 3
    pr = p.parse_args(["bg", "shell", "prune"])
    assert pr.action == "prune"


# ---------------------------------------------- Round 3: 预览/并发限制/清理

def test_status_has_preview(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    job = mgr.start('python -c "print(1,flush=True);print(2,flush=True)"')
    mgr.wait(job.job_id, timeout=10)
    view = mgr.status_dict(job.job_id)
    assert view["ok"] and "preview" in view
    assert "1" in view["preview"] or "2" in view["preview"]


def test_prune_disk_removes_finished(tmp_path, qxt_home):
    mgr = BackgroundShellManager(tmp_path)
    job = mgr.start('python -c "print(9,flush=True)"')
    mgr.wait(job.job_id, timeout=10)
    assert mgr.get(job.job_id) is not None
    n = mgr.prune_disk()
    assert n >= 1
    assert (tmp_path / "background_shell" / f"{job.job_id}.json").exists() is False
    # 运行中的不被清理
    j2 = mgr.start('python -c "import time;time.sleep(5)"')
    time.sleep(0.4)
    assert mgr.get(j2.job_id).status in ("queued", "running")


def test_max_running_limit(tmp_path, qxt_home, monkeypatch):
    monkeypatch.setenv("QXT_BG_SHELL_MAX", "1")
    mgr = BackgroundShellManager(tmp_path)
    j1 = mgr.start('python -c "import time;time.sleep(30)"')
    assert j1.job_id.startswith("shl-")
    with pytest.raises(RuntimeError):
        mgr.start('python -c "print(1)"')  # 已达并发上限, 拒绝启动
    # 上限信息体现在 list/status 里
    assert mgr.status_dict()["max_running"] == 1
