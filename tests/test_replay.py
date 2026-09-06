"""A4 测试: replay 引擎 (列会话 / 加载 Trajectory / 重建 messages / 渲染)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from qingxiaotuan.core.replay import (
    list_sessions, load_trajectory, reconstruct_messages, render_replay,
)
from qingxiaotuan.memory.sessions import SessionStore


def _write_session(home: Path, sid: str):
    store = SessionStore(home)
    store.rotate(sid, meta={"task": "示例任务"})
    store.append("user", {"message": {"role": "user", "content": "你好"}})
    store.append("agent_message_chunk", {"text": "你好"})
    store.append("agent_message_chunk", {"text": "，我是青小团"})
    store.append("tool_call", {"name": "bash", "arguments": "echo hi", "toolCallId": "t1"})
    store.append("tool_call_update", {"name": "bash", "status": "completed",
                                     "result": "hi", "toolCallId": "t1"})
    store.append("assistant", {"message": {"role": "assistant",
                                           "content": "你好，我是青小团"}})
    return store


def test_list_and_load(tmp_path):
    home = tmp_path / "home"
    _write_session(home, "abc123")
    rows = list_sessions(home)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "abc123"
    assert rows[0]["title"] == "示例任务"

    traj = load_trajectory("abc123", home)
    assert traj is not None
    assert traj.meta.get("task") == "示例任务"
    m = traj.metrics()
    assert m["tool_calls"] == 1
    assert m["user_turns"] == 1


def test_reconstruct_messages(tmp_path):
    home = tmp_path / "home"
    _write_session(home, "xyz789")
    msgs = reconstruct_messages("xyz789", home)
    # user + assistant (完整事件) 应被重建
    roles = [m.get("role") for m in msgs]
    assert "user" in roles
    assert "assistant" in roles


def test_render_replay_text(tmp_path):
    home = tmp_path / "home"
    _write_session(home, "render01")
    text = render_replay("render01", home)
    assert "会话回放" in text
    assert "bash" in text
    assert "✓" in text or "工具" in text


def test_prefix_match(tmp_path):
    home = tmp_path / "home"
    _write_session(home, "prefixABCDEFG")
    traj = load_trajectory("prefix", home)  # 前缀匹配
    assert traj is not None
    assert traj.session_id == "prefixABCDEFG"
