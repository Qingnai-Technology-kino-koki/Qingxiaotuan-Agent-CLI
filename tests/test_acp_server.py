"""ACP server 单测: 不依赖真实 LLM, 用 FakeAgent 验证协议行为。

覆盖: initialize 回执 + 初始化广播、prompt 流式 (agent_message_chunk / tool_call /
tool_call_update)、权限握手 (permission_request -> update(approved/denied))、cancel。

归属说明: 协议子集为青小团对 kimi-code ACP 理念的重新实现 (见 qingxiaotuan/acp/)。
"""

from __future__ import annotations

import json
import threading
import time

from qingxiaotuan.acp.server import AcpServer


class _CapturingWriter:
    def __init__(self):
        self._lines = []

    def write(self, s):
        self._lines.append(s)

    def flush(self):
        pass

    def messages(self):
        return [json.loads(l) for l in self._lines if l.strip()]


class FakeAgent:
    """行为可控的假 Agent: 流式 -> 请求权限 -> 工具调用 -> 完成。"""

    def __init__(self, confirm, deny_first=False):
        self.confirm = confirm
        self.deny_first = deny_first
        self.cancelled = False
        self.last_prompt = None

    def run(self, prompt, stream, on_token, on_tool, on_tool_result, on_error,
            session_id=None):
        self.last_prompt = prompt
        on_token("hi ")
        on_token("there")
        decision = self.confirm("run shell?")
        on_tool("run_shell", "ls -la")
        on_tool_result("run_shell", "file.txt")
        if not decision:
            on_error("permission denied by user")
            return "aborted"
        return "done:" + prompt

    def cancel(self):
        self.cancelled = True


def _make_server(agent_provider, writer):
    srv = AcpServer(
        agent_provider=agent_provider,
        agent_info={"name": "青小团", "version": "0.2.011"},
        model="deepseek/chat",
        model_info={"id": "chat", "provider": "deepseek"},
        workspace="/tmp/ws",
    )
    srv._writer = writer
    return srv


def test_initialize_emits_result_and_initialized():
    w = _CapturingWriter()
    srv = _make_server(lambda c: FakeAgent(c), w)
    srv._handle_initialize({}, req_id=1)
    msgs = w.messages()
    resp = [m for m in msgs if m.get("id") == 1][0]
    assert resp["result"]["sessionId"] == srv._session_id
    assert resp["result"]["model"] == "deepseek/chat"
    assert any(
        m.get("method") == "session/update" and m["params"].get("type") == "initialized"
        for m in msgs
    )
    # available_commands_update 也应广播 (kimi 的 session-scoped skills 快照)
    assert any(
        m.get("method") == "session/update"
        and m["params"].get("type") == "available_commands_update"
        for m in msgs
    )


def _drive_prompt(srv, writer, decision):
    """手动跑 prompt 线程, 待权限请求到达后回执 decision, 最后 join。"""
    srv._running = True
    t = threading.Thread(target=srv._run_agent, args=("do thing",), daemon=True)
    t.start()
    # 等待权限请求进入 pending
    for _ in range(200):
        if srv._pending_perm:
            break
        time.sleep(0.005)
    assert srv._pending_perm, "权限请求未进入 pending"
    tid = next(iter(srv._pending_perm))
    srv._handle_update(
        {"type": "permission_response", "toolCallId": tid, "decision": decision}, req_id=2
    )
    t.join(timeout=3)
    return writer.messages()


def test_prompt_streams_and_completes_on_approval():
    w = _CapturingWriter()
    srv = _make_server(lambda c: FakeAgent(c), w)
    msgs = _drive_prompt(srv, w, "approved")
    assert any(
        m.get("method") == "session/update"
        and m["params"].get("type") == "agent_message_chunk"
        and "hi " in m["params"].get("text", "")
        for m in msgs
    )
    assert any(
        m.get("method") == "session/update" and m["params"].get("type") == "tool_call"
        for m in msgs
    )
    assert any(
        m.get("method") == "session/update"
        and m["params"].get("type") == "tool_call_update"
        and m["params"].get("status") == "completed"
        for m in msgs
    )
    assert any(
        m.get("method") == "task/update" and m["params"].get("status") == "completed"
        for m in msgs
    )


def test_prompt_fails_on_denial():
    w = _CapturingWriter()
    srv = _make_server(lambda c: FakeAgent(c), w)
    msgs = _drive_prompt(srv, w, "denied")
    assert any(
        m.get("method") == "task/update" and m["params"].get("status") == "failed"
        for m in msgs
    )


def test_cancel_invokes_agent_cancel():
    w = _CapturingWriter()
    srv = _make_server(lambda c: FakeAgent(c), w)
    srv._agent = FakeAgent(lambda: True)
    srv._handle_cancel({}, req_id=3)
    assert srv._agent.cancelled is True


def test_confirm_returns_false_on_denied():
    w = _CapturingWriter()
    srv = _make_server(lambda c: FakeAgent(c), w)
    srv._running = True
    result = {}

    def waiter():
        result["decision"] = srv._confirm("ok?")

    th = threading.Thread(target=waiter, daemon=True)
    th.start()
    for _ in range(200):
        if srv._pending_perm:
            break
        time.sleep(0.005)
    tid = next(iter(srv._pending_perm))
    srv._handle_update(
        {"type": "permission_response", "toolCallId": tid, "decision": "denied"}, req_id=4
    )
    th.join(timeout=3)
    assert result["decision"] is False
