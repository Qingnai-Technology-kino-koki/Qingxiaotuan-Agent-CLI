"""A4 测试: 事件协议归一化 + Trajectory 重建/指标/序列化。"""

from __future__ import annotations

from qingxiaotuan.core.event_protocol import (
    AgentEvent, canonicalize, normalize_record,
    tool_call, tool_call_update, agent_message_chunk,
)
from qingxiaotuan.core.trajectory import Trajectory


def _sample_records():
    return [
        {"ts": 1.0, "type": "session.meta", "task": "修复登录 bug", "started_at": 1.0},
        {"ts": 1.1, "type": "user", "message": {"role": "user", "content": "帮我修登录"}},
        {"ts": 1.2, "type": "agent_message_chunk", "text": "好的"},
        {"ts": 1.3, "type": "agent_message_chunk", "text": "，我来查"},
        # 既有别名 tool_result (应被归一到 tool_call_update)
        {"ts": 1.4, "type": "tool_result", "name": "bash", "status": "completed",
         "result": "ok", "tool_call_id": "t1"},
        {"ts": 1.5, "type": "agent_message_chunk", "text": "已修复"},
        {"ts": 1.6, "type": "assistant", "message": {"role": "assistant",
         "content": "已修复登录问题"}},
        {"ts": 1.7, "type": "verify.passed", "round": 1},
        {"ts": 1.8, "type": "usage", "delta": {"prompt_tokens": 100, "completion_tokens": 20},
         "cost_usd": 0.001},
    ]


def test_canonicalize_alias():
    assert canonicalize("tool_result") == AgentEvent.TOOL_CALL_UPDATE
    assert canonicalize("user") == "user"


def test_normalize_record_legacy_alias():
    r = normalize_record({"type": "tool_result", "name": "bash", "status": "completed"})
    assert r["type"] == AgentEvent.TOOL_CALL_UPDATE


def test_trajectory_build_steps_and_metrics():
    traj = Trajectory.from_records(_sample_records(), session_id="s1")
    # 用户 1 条; 助手: chunk 合并成 1 条 + 完整 assistant 1 条 (chunk 已 flush, 跳过重复)
    user = [s for s in traj.steps if s.role == "user"]
    assistant = [s for s in traj.steps if s.kind == "message" and s.role == "assistant"]
    tools = [s for s in traj.steps if s.kind == "tool"]
    assert len(user) == 1
    # chunk "好的，我来查" + "已修复" 被 flush 成 1 条; 完整 assistant 事件因 chunk 已存在被跳过
    assert len(assistant) == 1
    assert assistant[0].content == "好的，我来查已修复"
    assert len(tools) == 1
    assert tools[0].tool == "bash"
    assert tools[0].status == "completed"
    m = traj.metrics()
    assert m["tool_calls"] == 1
    assert m["tool_ok"] == 1
    assert m["tool_success_rate"] == 1.0
    assert m["total_tokens"] == 120
    assert m["total_cost_usd"] == 0.001


def test_trajectory_json_and_markdown():
    traj = Trajectory.from_records(_sample_records(), session_id="s1")
    js = traj.to_json()
    assert "session_id" in js
    md = traj.to_markdown()
    assert "Trajectory" in md
    assert traj.summary()


def test_tool_call_update_helper():
    tc = tool_call("bash", "ls", "t9")
    assert tc["type"] == AgentEvent.TOOL_CALL
    tu = tool_call_update("t9", "completed", "ok")
    assert tu["status"] == "completed"
    chk = agent_message_chunk("hi")
    assert chk["text"] == "hi"
