"""Hooks 事件面扩展测试 (UserPromptSubmit / Stop / SubagentStop / PreCompact)。

覆盖:
- HOOK_EVENTS 事件表完整性 (8 事件)
- run_notify 派发通知类事件 (payload 落盘验证) + 拒绝未支持事件名
- run_user_prompt_submit: stdout 收集 / 空 / 禁用时空串
- ContextManager.needs_compact 预判
- agent.run() 集成: UserPromptSubmit stdout 注入本回合用户消息 + Stop 回合结束触发
"""

import json
import os
import sys

import pytest

from qingxiaotuan.context.manager import ContextManager
from qingxiaotuan.hooks import HookManager, HOOK_EVENTS
from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.base import ModelAdapter, ModelResponse

PY = sys.executable
FIX = os.path.join(os.path.dirname(__file__), "fixtures")
AUDIT = os.path.join(FIX, "hook_audit.py")


def make_cfg(block: dict) -> dict:
    return {"hooks": block}


# ------------------------------------------------------------------ 事件表

def test_hook_events_surface():
    # 对标 Claude Code 的 8 事件面
    assert set(HOOK_EVENTS) == {
        "PreToolUse", "PostToolUse", "UserPromptSubmit",
        "Stop", "SubagentStop", "PreCompact",
        "SessionStart", "SessionEnd",
    }


def test_run_notify_dispatches(tmp_path):
    audit_log = os.path.join(str(tmp_path), "notify.log")
    os.environ["HOOK_AUDIT_LOG"] = audit_log
    try:
        mgr = HookManager(make_cfg({
            "Stop": [{"matcher": "*", "command": [PY, AUDIT]}],
            "SubagentStop": [{"matcher": "*", "command": [PY, AUDIT]}],
            "PreCompact": [{"matcher": "*", "command": [PY, AUDIT]}],
        }), workspace=str(tmp_path))
        mgr.run_notify("Stop", {"reason": "stop"})
        mgr.run_notify("SubagentStop", {"task_id": "T1"})
        mgr.run_notify("PreCompact", {"trigger": "manual"})
        lines = [json.loads(l) for l in open(audit_log, encoding="utf-8") if l.strip()]
        names = [e.get("hook_event_name") for e in lines]
        assert names.count("Stop") == 1
        assert names.count("SubagentStop") == 1
        assert names.count("PreCompact") == 1
    finally:
        os.environ.pop("HOOK_AUDIT_LOG", None)


def test_run_notify_rejects_unknown_and_tool_events(tmp_path):
    audit_log = os.path.join(str(tmp_path), "none.log")
    os.environ["HOOK_AUDIT_LOG"] = audit_log
    try:
        # 即使配置了 PreToolUse 条目, 也不允许走通知通道派发
        mgr = HookManager(make_cfg({
            "PreToolUse": [{"matcher": "*", "command": [PY, AUDIT]}],
        }), workspace=str(tmp_path))
        mgr.run_notify("PreToolUse", {})
        mgr.run_notify("BogusEvent", {})
        assert not os.path.exists(audit_log)  # 一个都没执行
    finally:
        os.environ.pop("HOOK_AUDIT_LOG", None)


# ------------------------------------------------------------------ UserPromptSubmit

def test_prompt_submit_collects_stdout():
    mgr = HookManager(make_cfg({
        "UserPromptSubmit": [
            {"matcher": "*", "command": [PY, "-c", "print('CTX-A')"]},
            {"matcher": "*", "command": [PY, "-c", "print('CTX-B')"]},
        ],
    }))
    out = mgr.run_user_prompt_submit("帮我写个文件")
    assert "CTX-A" in out and "CTX-B" in out


def test_prompt_submit_empty_when_no_hooks_or_disabled():
    mgr = HookManager(make_cfg({"enabled": True}))
    assert mgr.run_user_prompt_submit("hi") == ""
    off = HookManager(make_cfg({
        "enabled": False,
        "UserPromptSubmit": [{"matcher": "*", "command": [PY, "-c", "print('x')"]}],
    }))
    assert off.run_user_prompt_submit("hi") == ""


# ------------------------------------------------------------------ needs_compact

def test_needs_compact_prediction():
    cm = ContextManager(keep_recent=2, budget_tokens=100)
    assert cm.needs_compact([{"role": "user", "content": "字" * 500}]) is True
    assert cm.needs_compact([{"role": "user", "content": "hi"}]) is False


# ------------------------------------------------------------------ agent.run() 集成

class MockModel(ModelAdapter):
    name = "mock"

    def __init__(self, content="done"):
        self.content = content
        self.calls = []

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls.append(messages)
        return ModelResponse(content=self.content)


def _build_agent(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    model = MockModel()
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", model, owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda _p: True)
    return agent, model


def test_agent_injects_prompt_submit_context(tmp_path, qxt_home):
    agent, model = _build_agent(tmp_path, qxt_home)
    agent.ctx.hooks = HookManager(make_cfg({
        "UserPromptSubmit": [{"matcher": "*", "command": [PY, "-c", "print('HOOKCTX-42')"]}],
    }), workspace=str(tmp_path))
    agent.run("随便说点什么", stream=False)
    user_msgs = [m for m in model.calls[0] if m.get("role") == "user"]
    assert any("HOOKCTX-42" in str(m.get("content")) for m in user_msgs)


def test_agent_fires_stop_on_finish(tmp_path, qxt_home):
    audit_log = os.path.join(str(tmp_path), "stop.log")
    os.environ["HOOK_AUDIT_LOG"] = audit_log
    try:
        agent, _ = _build_agent(tmp_path, qxt_home)
        agent.ctx.hooks = HookManager(make_cfg({
            "Stop": [{"matcher": "*", "command": [PY, AUDIT]}],
        }), workspace=str(tmp_path))
        answer = agent.run("收尾", stream=False)
        assert answer == "done"
        lines = [json.loads(l) for l in open(audit_log, encoding="utf-8") if l.strip()]
        stops = [e for e in lines if e.get("hook_event_name") == "Stop"]
        assert len(stops) == 1
    finally:
        os.environ.pop("HOOK_AUDIT_LOG", None)


def test_agent_no_stop_without_hooks(tmp_path, qxt_home):
    # 未配置 hooks 时主循环零额外开销、行为不变
    agent, model = _build_agent(tmp_path, qxt_home)
    answer = agent.run("plain input", stream=False)
    assert answer == "done"
    assert len(model.calls) == 1
