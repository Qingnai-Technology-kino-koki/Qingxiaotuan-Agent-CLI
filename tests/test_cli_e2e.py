"""M8: CLI 端到端测试 —— 真实子进程跑 `qxt run`, 连本地 mock server 完成一次 headless 任务。

验证: 命令行入口 → build_kernel → create_agent → Agent.run → 真实 HTTP 模型调用
→ 工具执行 → 输出回答, 整条链路在独立子进程里闭环 (离线, 不访问外网)。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import _tool_call  # noqa: F401  (共享 mock 端点)


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


def _qxt_bin() -> str:
    """venv 里安装的 qxt 控制台脚本 (真实入口接线)。"""
    return str(Path(sys.executable).resolve().parent / "qxt.exe")


def test_cli_run_headless_with_mock_server(tmp_path, qxt_home, mock_server):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.txt").write_text("CLI 端到端", encoding="utf-8")
    home = tmp_path / "home"
    server = mock_server([
        {"tool_calls": [_tool_call("read_file", {"path": "hello.txt"})],
         "finish_reason": "tool_calls"},
        {"content": "CLI 端到端测试通过"},
    ])
    _write_config(home, server.port)

    env = dict(os.environ)
    env["QXT_HOME"] = str(home)
    env["QXT_API_KEY"] = "test-key"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    proc = subprocess.run(
        [_qxt_bin(), "run", "读 hello.txt", "--no-stream"],
        cwd=str(ws), env=env, capture_output=True, text=True, timeout=180,
    )

    assert proc.returncode == 0, f"exit={proc.returncode}\nSTDERR:\n{proc.stderr[-1500:]}"
    assert "CLI 端到端测试通过" in proc.stdout
    # 子进程确实走了工具调用 (mock 收到 2 轮请求)
    assert len(server.requests) == 2
    assert not server.requests[0].get("stream")


def test_cli_run_streaming_with_mock_server(tmp_path, qxt_home, mock_server):
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "home"
    server = mock_server([
        {"content": "流式输出已送达"},
    ])
    _write_config(home, server.port)

    env = dict(os.environ)
    env["QXT_HOME"] = str(home)
    env["QXT_API_KEY"] = "test-key"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    proc = subprocess.run(
        [_qxt_bin(), "run", "用流式回答"],
        cwd=str(ws), env=env, capture_output=True, text=True, timeout=180,
    )

    assert proc.returncode == 0, f"exit={proc.returncode}\nSTDERR:\n{proc.stderr[-1500:]}"
    assert "流式输出已送达" in proc.stdout
    assert server.requests[0]["stream"] is True
