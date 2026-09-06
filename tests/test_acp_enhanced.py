"""ACP 融合层测试 (fusion.acp_enhanced) —— 不依赖任何外部 IDE/网络。

覆盖:
- 协议版本协商 negotiate_version
- LineBuffer 增量字节帧解析（部分/拼接）
- parse_frame 错误容忍
- read_messages_robust 跳过畸形帧
- AcpServer enhanced 模式: initialize 携带 protocolVersion、富事件 agent_thought_chunk、
  permission_request 带 options 审批选项集；以及关闭时的原生兼容行为。
"""

from __future__ import annotations

import io
import json
import threading
import time

from qingxiaotuan.acp.protocol import LineBuffer, parse_frame, read_messages_robust
from qingxiaotuan.acp.server import AcpServer
from qingxiaotuan.acp.version import CURRENT_VERSION, negotiate_version


# --------------------------------------------------------------------- 版本协商

def test_negotiate_version():
    assert negotiate_version(1) == 1
    assert negotiate_version(5) == 1  # 高于支持版本, 取最高支持 1
    assert negotiate_version(0) == CURRENT_VERSION  # 低于最低, 返回当前
    assert negotiate_version(-3) == CURRENT_VERSION


# --------------------------------------------------------------------- 帧解析

def test_line_buffer_partial():
    lb = LineBuffer()
    out = lb.feed(b'{"a":1}\n{"b":2}')
    assert [x.decode() for x in out] == ['{"a":1}']
    out = lb.feed(b'\n{"c":3}\n')
    assert [x.decode() for x in out] == ['{"b":2}', '{"c":3}']
    assert lb.flush() == []


def test_line_buffer_flush_residual():
    lb = LineBuffer()
    lb.feed(b'partial-no-newline')
    assert lb.flush() == [b'partial-no-newline']


def test_parse_frame_valid():
    f = parse_frame('{"jsonrpc":"2.0","id":1,"method":"x"}')
    assert f["id"] == 1 and f["method"] == "x" and f["error"] is None


def test_parse_frame_bad_json():
    f = parse_frame('not json')
    assert f["error"] is not None and f["error"]["code"] == -32700


def test_parse_frame_non_dict():
    f = parse_frame('[1,2,3]')
    assert f["error"] is not None and f["error"]["code"] == -32600


def test_read_messages_robust_skips_bad():
    lines = ['{"id":1,"method":"a"}', 'garbage', '{"id":2,"method":"b"}']
    parsed = list(read_messages_robust(iter(lines)))
    assert [p["method"] for p in parsed] == ["a", "b"]


# --------------------------------------------------------------------- AcpServer

class _FakeAgent:
    def __init__(self, confirm, recorder):
        self._confirm = confirm
        self._recorder = recorder

    def run(self, prompt, stream=False, on_token=None, on_reason=None,
            on_tool=None, on_tool_result=None, on_error=None, session_id=None, **kw):
        if on_reason:
            on_reason("thinking step")
        if on_token:
            on_token("final answer")
        decision = self._confirm("may I?")
        self._recorder["decision"] = decision
        return "done"

    def cancel(self):
        pass


class _ListWriter:
    def __init__(self):
        self.lines = []

    def write(self, s):
        self.lines.append(s)

    def flush(self):
        pass


def _build_enhanced(enhanced: bool, recorder):
    def provider(confirm):
        return _FakeAgent(confirm, recorder)

    return AcpServer(
        agent_provider=provider,
        agent_info={"name": "x", "version": "1"},
        model="m",
        model_info={"id": "m", "provider": "p"},
        enhanced=enhanced,
    )


def _run_server(server, inputs):
    """把 inputs (list of dict) 作为 stdin 喂入, 在内存中跑完整 serve 循环。"""
    reader = io.StringIO("\n".join(json.dumps(m) for m in inputs) + "\n")
    writer = _ListWriter()
    server.serve(reader, writer)
    return writer.lines


def _drive_prompt(server, writer, decision):
    """手动驱动 prompt 线程, 待权限请求进入 pending 后回执 decision, 最后 join。

    与 ``test_acp_server._drive_prompt`` 同构；避免经 ``serve`` 一次性喂入所有帧
    造成的「权限回执早于权限请求」竞态（background thread 尚未填充 ``_pending_perm``）。
    """
    server._writer = writer
    server._running = True
    t = threading.Thread(target=server._run_agent, args=("hi",), daemon=True)
    t.start()
    for _ in range(400):
        if server._pending_perm:
            break
        time.sleep(0.005)
    assert server._pending_perm, "权限请求未进入 pending"
    tid = next(iter(server._pending_perm))
    server._handle_update(
        {"type": "permission_response", "toolCallId": tid, "decision": decision}, req_id=99
    )
    t.join(timeout=3)
    return writer.lines


def test_enhanced_initialize_negotiates_version():
    recorder = {}
    server = _build_enhanced(True, recorder)
    out = _run_server(server, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown"},
    ])
    init = json.loads(out[0])
    assert init["id"] == 1
    assert init["result"]["protocolVersion"] == 1


def test_non_enhanced_initialize_no_protocol_version():
    recorder = {}
    server = _build_enhanced(False, recorder)
    out = _run_server(server, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown"},
    ])
    init = json.loads(out[0])
    assert "protocolVersion" not in init["result"]


def test_enhanced_emits_thought_and_permission_options():
    recorder = {}
    server = _build_enhanced(True, recorder)
    out = _drive_prompt(server, _ListWriter(), "approve_once")
    messages = [json.loads(l) for l in out]
    types = [m.get("params", {}).get("type") for m in messages
             if m.get("method") == "session/update"]
    # 富事件: 推理过程以 agent_thought_chunk 回报
    assert "agent_thought_chunk" in types
    # 权限请求携带 options 审批选项集
    perm = next(m for m in messages
                if m.get("method") == "session/update"
                and m["params"].get("type") == "permission_request")
    assert perm["params"]["options"] == ["approve_once", "approve_always", "reject", "plan_review"]
    # 权限确认后 agent 正常完成, confirm 返回 True
    assert recorder["decision"] is True


def test_enhanced_permission_reject_blocks():
    recorder = {}
    server = _build_enhanced(True, recorder)
    _drive_prompt(server, _ListWriter(), "reject")
    assert recorder["decision"] is False


def test_non_enhanced_permission_request_has_no_options():
    recorder = {}
    server = _build_enhanced(False, recorder)
    out = _drive_prompt(server, _ListWriter(), "approved")
    messages = [json.loads(l) for l in out]
    perm = next(m for m in messages
                if m.get("method") == "session/update"
                and m["params"].get("type") == "permission_request")
    assert "options" not in perm["params"]
    assert recorder["decision"] is True
