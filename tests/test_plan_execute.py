"""规划/执行分离 + 卡住升级 的 Agent 集成测试 (复用 test_router 的 Fake 设施, 不触网)。"""

from __future__ import annotations

import os

import pytest

from qingxiaotuan.config import Config
from qingxiaotuan.models.base import ModelCapabilities, ModelResponse, ToolCall
from qingxiaotuan.core.agent import Agent

from tests.test_router import (
    _FakeContext,
    _FakeKernel,
    _FakeRegistry,
    _VISION_MODELS,
)


class _DummyAuto:
    """最小 AutoRouter 契约桩: 按需返回 override / 记录观察调用。"""
    enabled = True
    plan_execute = True
    escalate = False

    def __init__(self, override: int = 0):
        self._override = override
        self.observed = 0

    def decide_override(self, route_session):
        return self._override

    def observe_turn(self, route_session, tool_messages=None):
        self.observed += 1


class _StubAgent:
    """满足 LoopProvider._maybe_override_model 需要的 agent 最小面。"""

    def __init__(self, override: int = 0):
        self._auto = _DummyAuto(override=override)
        self._route_session = "session"
        self.model = type("M", (), {"capabilities": type("C", (), {"vision": False})()})()
        self.switched = []

    def _maybe_route_model(self, user_input, override=None, has_images=False):
        self.switched.append((user_input, override, has_images))


def test_maybe_override_short_circuits_when_auto_disabled():
    from qingxiaotuan.core.loop_provider import LoopProvider
    agent = _StubAgent(override=2)
    agent._auto.enabled = False  # 关路由时不应切换模型
    LoopProvider._maybe_override_model(agent, "task")
    assert agent.switched == []


def test_maybe_override_switches_when_override_nonzero():
    from qingxiaotuan.core.loop_provider import LoopProvider
    agent = _StubAgent(override=2)
    LoopProvider._maybe_override_model(agent, "task")
    assert agent.switched == [("task", 2, False)]


def test_maybe_override_noop_when_override_zero():
    from qingxiaotuan.core.loop_provider import LoopProvider
    agent = _StubAgent(override=0)
    LoopProvider._maybe_override_model(agent, "task")
    assert agent.switched == []


_TIER3 = {"deepseek-reasoner", "gpt-4o", "grok-3", "claude-3-opus-20240229"}
_TIER1 = {
    "deepseek-v4-flash-free", "llama-3.3-70b-versatile",
    "glm-4-flash", "qwen-plus", "doubao-1.5-pro-256k",
}


@pytest.fixture(autouse=True)
def isolated_qxt_home(monkeypatch, tmp_path):
    """隔离 QXT_HOME, 避免读到真实 ~/.qingxiaotuan/config.yaml (如 opencode-zen 配置),
    保证路由测试的当前模型是默认 deepseek/deepseek-chat, 且 set_user 只写临时目录。"""
    monkeypatch.setenv("QXT_HOME", str(tmp_path))
    yield


class _SeqModel:
    name = "openai-compat"

    def __init__(self, model_name: str, vision: bool, n_tool_calls: int = 1):
        self.model = model_name
        self.capabilities = ModelCapabilities(vision=vision)
        self._n = n_tool_calls
        self.chat_n = 0

    def chat(self, messages, tools=None, stream=False, on_token=None, on_reason=None):
        self.chat_n += 1
        if self.chat_n <= self._n:
            return ModelResponse(content="", tool_calls=[ToolCall(id="1", name="run", arguments="{}")])
        return ModelResponse(content="done")


def _build(n_tool_calls: int = 1):
    model = _SeqModel("deepseek-chat", False, n_tool_calls=n_tool_calls)
    kernel = _FakeKernel(model, _FakeRegistry())
    agent = Agent(kernel, Config(), workspace=os.getcwd(), context_manager=_FakeContext())
    return agent, model, kernel


def _switcher_recording(kernel):
    switched = []

    def switcher(k, overrides):
        switched.append((overrides["provider"], overrides["model"]))
        vision = overrides["model"] in _VISION_MODELS
        kernel.provide("model_adapter", _SeqModel(overrides["model"], vision))

    return switcher, switched


def test_plan_execute_switches_strong_then_cheap(monkeypatch):
    from qingxiaotuan.models.router import ModelRouter
    monkeypatch.setattr(
        ModelRouter, "available_provider_names",
        staticmethod(lambda: ["deepseek", "groq", "zhipu", "qwen", "doubao",
                               "openai", "gemini", "anthropic", "moonshot", "mistral", "xai"]),
    )

    agent, _m, kernel = _build(n_tool_calls=1)  # 首轮有工具调用 → 进入执行轮
    switcher, switched = _switcher_recording(kernel)
    agent._model_switcher = switcher
    agent.config.set_user("router.plan_execute", True)
    agent.run("实现一个函数并写测试")

    strong = [(p, m) for p, m in switched if m in _TIER3]
    cheap = [(p, m) for p, m in switched if m in _TIER1]
    assert strong, f"规划轮应切到强模型, 实际切换序列={switched}"
    assert cheap, f"执行轮应切到便宜模型, 实际切换序列={switched}"


def test_escalate_on_stuck_switches_back_to_strong(monkeypatch):
    from qingxiaotuan.models.router import ModelRouter
    from qingxiaotuan.tools.base import ToolResult

    class _FailingRegistry(_FakeRegistry):
        def dispatch(self, name, args, ctx):
            return ToolResult(status="error", content="boom", tool_name=name)

    monkeypatch.setattr(
        ModelRouter, "available_provider_names",
        staticmethod(lambda: ["deepseek", "groq", "zhipu", "qwen", "doubao",
                               "openai", "gemini", "anthropic", "moonshot", "mistral", "xai"]),
    )

    # 便宜模型每轮都执行失败 → stuck 累积 → 触发升级强模型救场
    model = _SeqModel("deepseek-chat", False, n_tool_calls=4)
    kernel = _FakeKernel(model, _FailingRegistry())
    agent = Agent(kernel, Config(), workspace=os.getcwd(), context_manager=_FakeContext())
    switcher, switched = _switcher_recording(kernel)
    agent._model_switcher = switcher
    agent.config.set_user("router.plan_execute", True)
    agent.config.set_user("router.stuck_threshold", 3)
    agent.run("重构这个模块")

    strong = [(p, m) for p, m in switched if m in _TIER3]
    cheap = [(p, m) for p, m in switched if m in _TIER1]
    assert strong, f"应出现过强模型 (规划/救场), 实际={switched}"
    assert cheap, f"执行轮应先用便宜模型, 实际={switched}"
    # 救场: 便宜模型先出现, 之后才出现升级的强模型
    first_cheap = min(i for i, (p, m) in enumerate(switched) if m in _TIER1)
    last_strong = max(i for i, (p, m) in enumerate(switched) if m in _TIER3)
    assert last_strong > first_cheap, f"升级应发生在便宜执行之后, 序列={switched}"
