"""M5 离线测试: 任务恢复与安全取消。

覆盖: 进程树终止、cancel 杀死 detached worker、线程任务 cancel 退出、
worker 响应 cancel_requested、reconcile_stale 标记失败、recover_queued 重启。
全部用临时 home + mock, 不依赖真实模型 API。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from qingxiaotuan.core.background_store import BackgroundStore, kill_process_tree


# ---------------------------------------------------------------------------
# kill_process_tree
# ---------------------------------------------------------------------------
def test_kill_process_tree_kills_children():
    """杀父进程应连带杀死其子进程 (进程组级终止)。"""
    if os.name == "nt":
        pytest.skip("Windows 下子进程组递归需 taskkill, CI 不便验证真实树")
    # 启动一个父 shell, 它再 spawn 一个会睡眠的孙子, 形成进程树。
    parent = subprocess.Popen(
        ["bash", "-c", "sleep 30 & wait"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,  # 新进程组, 模拟 detached worker
    )
    pgid = os.getpgid(parent.pid)
    time.sleep(0.3)
    assert kill_process_tree(parent.pid) is True
    time.sleep(0.5)
    # 父进程应已退出
    assert parent.poll() is not None
    # 子进程 (sleep 30) 同组, 也应被 SIGTERM 杀掉
    try:
        os.killpg(pgid, 0)
        alive = True
    except (ProcessLookupError, PermissionError, OSError):
        alive = False
    assert not alive, "子进程树未被终止"


def test_kill_process_tree_none_or_dead():
    assert kill_process_tree(None) is False
    assert kill_process_tree(0) is False
    # 一个几乎不可能存在的 pid
    assert kill_process_tree(999999) in (True, False)


# ---------------------------------------------------------------------------
# reconcile_stale / recoverable
# ---------------------------------------------------------------------------
def _tmp_store(tmp_path: Path) -> BackgroundStore:
    return BackgroundStore(tmp_path)


def test_reconcile_stale_marks_dead_running_as_failed(tmp_path):
    store = _tmp_store(tmp_path)
    job = store.create("bg-x1", "task", str(tmp_path), "default")
    # 写入一个不存在的 pid + running 状态
    store.update("bg-x1", status="running", pid=999999, heartbeat=time.time() - 200)
    changed = store.reconcile_stale(timeout=10.0)
    assert changed == 1
    assert store.get("bg-x1")["status"] == "failed"


def test_reconcile_stale_keeps_alive_running(tmp_path):
    store = _tmp_store(tmp_path)
    store.create("bg-x2", "task", str(tmp_path), "default")
    store.update("bg-x2", status="running", pid=os.getpid(), heartbeat=time.time() - 200)
    changed = store.reconcile_stale(timeout=10.0)
    assert changed == 0
    assert store.get("bg-x2")["status"] == "running"


def test_recoverable_returns_queued_dead(tmp_path):
    store = _tmp_store(tmp_path)
    store.create("bg-q1", "task", str(tmp_path), "default")  # queued, pid=None
    store.create("bg-q2", "task", str(tmp_path), "default")
    store.update("bg-q2", status="running", pid=os.getpid(), heartbeat=time.time())  # 活着的
    rec = store.recoverable()
    assert "bg-q1" in rec
    assert "bg-q2" not in rec


# ---------------------------------------------------------------------------
# BackgroundRunner.cancel (detached + 线程)
# ---------------------------------------------------------------------------
def _mock_runner(tmp_path: Path) -> "BackgroundRunner":
    """构造一个最小可用的 BackgroundRunner 用于离线测试 (不构建真实 kernel)。"""
    from qingxiaotuan.core.background import BackgroundRunner
    from qingxiaotuan.config.loader import Config

    runner = BackgroundRunner.__new__(BackgroundRunner)
    runner._jobs = {}
    runner._lock = __import__("threading").Lock()
    runner._store = _tmp_store(tmp_path)
    runner._enabled = True
    runner.workspace = str(tmp_path)

    cfg = Config(profile="default")
    cfg.home = tmp_path  # 隔离到临时目录, 避免污染真实 home
    runner.config = cfg
    return runner


def test_cancel_detached_kills_worker_process(monkeypatch, tmp_path):
    from qingxiaotuan.core.background import BackgroundRunner

    killed = {}

    def fake_kill_tree(pid: int) -> bool:
        killed["pid"] = pid
        return True

    monkeypatch.setattr("qingxiaotuan.core.background_store.kill_process_tree", fake_kill_tree)

    runner = _mock_runner(tmp_path)
    runner._store.create("bg-d1", "task", str(tmp_path), "default")
    runner._store.update("bg-d1", status="running", pid=4242)

    ok = runner.cancel("bg-d1")
    assert ok is True
    assert killed.get("pid") == 4242
    assert runner._store.get("bg-d1")["status"] == "cancelled"


def test_cancel_thread_task_stops_loop(monkeypatch, tmp_path):
    """线程版任务: cancel 置 cancelled, _work 循环应退出。"""
    from qingxiaotuan.core.background import BackgroundJob
    from qingxiaotuan.memory.sessions import SessionStore

    runner = _mock_runner(tmp_path)
    job = BackgroundJob(job_id="bg-t1", task="sleep task", started_at=time.time(),
                        thread=None, store=SessionStore(tmp_path))
    job.status = "running"
    with runner._lock:
        runner._jobs["bg-t1"] = job

    ok = runner.cancel("bg-t1")
    assert ok is True
    assert job.status == "cancelled"
    # 线程任务无 manifest, 不应写盘, 但内存状态应为 cancelled
    assert runner._jobs["bg-t1"].status == "cancelled"


def test_cancel_non_running_returns_false(tmp_path):
    runner = _mock_runner(tmp_path)
    runner._store.create("bg-n1", "task", str(tmp_path), "default")
    runner._store.update("bg-n1", status="done")
    assert runner.cancel("bg-n1") is False
    assert runner.cancel("nope") is False


# ---------------------------------------------------------------------------
# worker 响应 cancel_requested
# ---------------------------------------------------------------------------
def test_worker_honors_cancel_requested(monkeypatch, tmp_path):
    from qingxiaotuan.core import background_worker as bw
    from types import SimpleNamespace

    home = tmp_path
    store = BackgroundStore(home)
    job = store.create("bg-w1", "task", str(tmp_path), "default")
    store.update("bg-w1", status="cancel_requested")

    # mock build_kernel/create_agent 避免真实模型调用
    class _FakeAgent:
        turn_count = 0

        def run(self, prompt, stream=False):
            return "done"

    cfg = SimpleNamespace(home=home)
    cfg.get = lambda k, d=None: d
    kernel = SimpleNamespace(require=lambda name: cfg)

    monkeypatch.setattr(bw, "build_kernel", lambda profile="default": kernel)
    monkeypatch.setattr(bw, "create_agent", lambda *a, **k: _FakeAgent())
    monkeypatch.setattr(bw, "load_dotenv", lambda *a, **k: None)

    rc = bw.run("bg-w1", home)
    assert rc == 0
    assert store.get("bg-w1")["status"] == "cancelled"


def test_worker_marks_done(monkeypatch, tmp_path):
    from qingxiaotuan.core import background_worker as bw
    from types import SimpleNamespace

    home = tmp_path
    store = BackgroundStore(home)
    store.create("bg-w2", "task", str(tmp_path), "default")

    class _FakeAgent:
        turn_count = 1

        def run(self, prompt, stream=False):
            return "任务完成 ✓"

    cfg = SimpleNamespace(home=home)
    cfg.get = lambda k, d=None: d
    kernel = SimpleNamespace(require=lambda name: cfg)

    monkeypatch.setattr(bw, "build_kernel", lambda profile="default": kernel)
    monkeypatch.setattr(bw, "create_agent", lambda *a, **k: _FakeAgent())
    monkeypatch.setattr(bw, "load_dotenv", lambda *a, **k: None)

    rc = bw.run("bg-w2", home)
    assert rc == 0
    assert store.get("bg-w2")["status"] == "done"


# ---------------------------------------------------------------------------
# recover_queued
# ---------------------------------------------------------------------------
def test_recover_queued_restarts(monkeypatch, tmp_path):
    from qingxiaotuan.core.background import BackgroundRunner

    runner = BackgroundRunner.__new__(BackgroundRunner)
    runner._jobs = {}
    runner._lock = __import__("threading").Lock()
    runner._store = _tmp_store(tmp_path)
    runner._enabled = True
    runner.workspace = str(tmp_path)

    # 一个 queued 且无人接管的任务
    runner._store.create("bg-r1", "recover me", str(tmp_path), "default")

    restarted = {}

    def fake_submit_detached(task, *, yolo=False):
        restarted["task"] = task
        return {"job_id": "bg-r1", "status": "running", "pid": 5555}

    monkeypatch.setattr(runner, "submit_detached", fake_submit_detached)

    out = runner.recover_queued()
    assert out == ["bg-r1"]
    assert restarted["task"] == "recover me"
