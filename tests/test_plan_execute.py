"""规划/执行分离 + 卡住升级 的 Agent 集成测试 (复用 test_router 的 Fake 设施, 不触网)。"""

from __future__ import annotations

import os

from qingxiaotuan.config import Config
from qingxiaotuan.models.base import ModelCapabilities, ModelResponse, ToolCall
from qingxiaotuan.core.agent import Agent

from tests.test_router import (
    _FakeContext,
    _FakeKernel,
    _FakeRegistry,
    _VISION_MODELS,
)


_TIER3 = {"deepseek-reasoner", "gpt-4o", "grok-3", "claude-3-opus-20240229"}
_TIER1 = {
    "deepseek-v4-flash-free", "llama-3.3-70b-versatile",
    "glm-4-flash", "qwen-plus", "doubao-1.5-pro-256k",
}


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


def test_plan_execute_switches_strong_then_cheap():
    from qingxiaotuan.models.router import ModelRouter
    import pytest
    pytest.MonkeyPatch().setattr(
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


def test_escalate_on_stuck_switches_back_to_strong():
    from qingxiaotuan.models.router import ModelRouter
    import pytest
    from qingxiaotuan.tools.base import ToolResult

    class _FailingRegistry(_FakeRegistry):
        def dispatch(self, name, args, ctx):
            return ToolResult(status="error", content="boom", tool_name=name)

    pytest.MonkeyPatch().setattr(
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
