"""M8: worker 子进程端到端测试 —— 真实 `background_worker` 独立进程跑任务。

验证: 提交任务 → 独立 worker 进程读取 manifest → 连 mock server 完成一轮
模型调用 → 检测到【已完成】标记 → 状态落盘为 done。跨进程全链路 (离线)。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import _tool_call  # noqa: F401  (共享 mock 端点)

from qingxiaotuan.core.background_store import BackgroundStore


def _write_config(home: Path, port: int) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "model:\n"
        "  provider: openai-compatible\n"
        f"  base_url: http://127.0.0.1:{port}/v1\n"
        "  model: mock\n"
        "  api_key_env: QXT_API_KEY\n",
        encoding="utf-8",
    )


def _wait_status(store: BackgroundStore, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get(job_id)
        if job and job.get("status") in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise TimeoutError(f"worker 未在 {timeout}s 内结束: {store.get(job_id)}")


def test_worker_subprocess_runs_task_to_done(tmp_path, qxt_home, mock_server):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "note.txt").write_text("后台任务数据", encoding="utf-8")
    home = tmp_path / "home"
    server = mock_server([
        {"tool_calls": [_tool_call("read_file", {"path": "note.txt"})],
         "finish_reason": "tool_calls"},
        {"content": "已读取: 后台任务数据\n【已完成】后台任务完成"},
    ])
    _write_config(home, server.port)

    store = BackgroundStore(home)
    job = store.create("worker-e2e-001", "读取 note.txt", str(ws), "default")

    env = dict(os.environ)
    env["QXT_HOME"] = str(home)
    env["QXT_API_KEY"] = "test-key"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    proc = subprocess.run(
        [sys.executable, "-m", "qingxiaotuan.core.background_worker", job["job_id"], str(home)],
        cwd=str(ws), env=env, capture_output=True, text=True, timeout=180,
    )

    assert proc.returncode == 0, f"exit={proc.returncode}\nSTDERR:\n{proc.stderr[-1500:]}"
    final = _wait_status(store, job["job_id"])
    assert final["status"] == "done"
    assert "后台任务完成" in final.get("result", "")
    # worker 确实走了工具调用 (mock 收到 2 轮请求)
    assert len(server.requests) == 2
    assert not server.requests[0].get("stream")
    # 会话事件流里应有 job.done 记录
    session = store.get(job["job_id"]).get("session_file")
    if session and Path(session).exists():
        assert any("job.done" in line for line in Path(session).read_text(encoding="utf-8").splitlines())


def test_worker_subprocess_marks_failed_on_error(tmp_path, qxt_home, mock_server):
    """mock 端返回 500, worker 应把任务标记为 failed 并返回非零。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "home"
    server = mock_server([])  # 空脚本 → 500
    _write_config(home, server.port)

    store = BackgroundStore(home)
    job = store.create("worker-e2e-002", "必然失败的任务", str(ws), "default")

    env = dict(os.environ)
    env["QXT_HOME"] = str(home)
    env["QXT_API_KEY"] = "test-key"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    proc = subprocess.run(
        [sys.executable, "-m", "qingxiaotuan.core.background_worker", job["job_id"], str(home)],
        cwd=str(ws), env=env, capture_output=True, text=True, timeout=180,
    )

    assert proc.returncode == 1
    final = _wait_status(store, job["job_id"])
    assert final["status"] == "failed"
    assert final.get("error")
