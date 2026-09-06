"""verify_loop 接入 agent 主循环的回归测试。

核心验证:
1. 默认 (config 无 verify) 不触发 —— 行为不变。
2. verify.enabled 但本轮无写工具 —— 不触发。
3. verify.enabled + 写工具, 验证失败 —— 把错误摘要作为 user 消息注入 (自修复)。
4. verify.enabled + 写工具, 验证通过 —— 仅提示, 不注入消息。
5. 验证在 agent.workspace 中执行, 且使用 VerifyConfig。
"""

import contextlib

import pytest

from qingxiaotuan.core import verify_loop as vl
from qingxiaotuan.core.agent import Agent


class _FakeAgent:
    """最小鸭子类型 Agent, 仅暴露 _run_verify_loop 依赖的属性/方法。"""

    def __init__(self, config):
        self.config = config
        self.workspace = "/tmp/fake-ws"
        self.messages = []

    def _session_append(self, *a, **k):
        pass


@contextlib.contextmanager
def _patch_verify_once(result):
    original = vl.verify_once
    try:
        vl.verify_once = lambda *a, **k: result
        yield
    finally:
        vl.verify_once = original


def _fail_round():
    c = vl.CheckResult(name="test", cmd="pytest", passed=False,
                       output="FAILED x.py", returncode=1)
    return vl.VerifyRound(round_num=1, checks=[c])


def _pass_round():
    c = vl.CheckResult(name="test", cmd="pytest", passed=True, output="", returncode=0)
    return vl.VerifyRound(round_num=1, checks=[c])


def test_default_config_does_not_trigger():
    agent = _FakeAgent({})  # 无 verify 键
    Agent._run_verify_loop(agent, [{"function": {"name": "write_file"}}])
    assert agent.messages == [], "默认配置不应触发验证闭环"


def test_verify_disabled_does_not_trigger():
    agent = _FakeAgent({"verify": {"enabled": False}})
    Agent._run_verify_loop(agent, [{"function": {"name": "write_file"}}])
    assert agent.messages == []


def test_no_write_tool_does_not_trigger():
    agent = _FakeAgent({"verify": {"enabled": True, "auto": True}})
    Agent._run_verify_loop(agent, [{"function": {"name": "read_file"}}])
    assert agent.messages == []


def test_failure_injects_selfheal_message():
    agent = _FakeAgent({"verify": {"enabled": True, "auto": True}})
    with _patch_verify_once(_fail_round()):
        Agent._run_verify_loop(agent, [{"function": {"name": "write_file"}}])
    assert len(agent.messages) == 1
    msg = agent.messages[0]
    assert msg["role"] == "user"
    assert "[验证闭环]" in msg["content"]
    assert "FAILED x.py" in msg["content"]


def test_pass_only_notifies_no_inject():
    agent = _FakeAgent({"verify": {"enabled": True, "auto": True}})
    captured = []
    with _patch_verify_once(_pass_round()):
        Agent._run_verify_loop(agent, 
            [{"function": {"name": "edit_file"}}], on_token=captured.append
        )
    assert agent.messages == [], "通过时不应注入 user 消息"
    assert any("全部通过" in t for t in captured), "通过时应提示"


def test_verify_once_invoked_in_workspace():
    agent = _FakeAgent({"verify": {"enabled": True, "auto": True}})
    seen = {}

    def _fake_verify(workspace, cfg):
        seen["workspace"] = workspace
        seen["cfg"] = cfg
        return _pass_round()

    original = vl.verify_once
    vl.verify_once = _fake_verify
    try:
        Agent._run_verify_loop(agent, [{"function": {"name": "write_file"}}])
    finally:
        vl.verify_once = original
    assert seen["workspace"] == agent.workspace
    assert isinstance(seen["cfg"], vl.VerifyConfig)
