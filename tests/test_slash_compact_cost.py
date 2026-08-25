"""/compact 与 /cost 斜杠命令 + 成本估算 (对标 Claude Code)。"""

import json

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.base import ModelAdapter, ModelResponse
from qingxiaotuan.models.router import estimate_cost


class MockModel(ModelAdapter):
    name = "mock"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls.append(messages)
        resp = self.script.pop(0)
        if stream and resp.content and on_token:
            on_token(resp.content)
        return resp


def _build_agent(tmp_path, qxt_home, script):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", MockModel(script), owner="test")
    return Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)


def test_agent_compact_folds_old_history(tmp_path, qxt_home):
    """Agent.compact() 手动折叠旧历史, 保留最近消息。"""
    agent = _build_agent(tmp_path, qxt_home, [])
    agent.messages = [{"role": "system", "content": "sys"}]
    for i in range(20):
        agent.messages.append({"role": "user", "content": f"历史消息{i}" * 30})
    before = len(agent.messages)
    dropped = agent.compact()
    assert dropped > 0
    assert len(agent.messages) < before
    assert agent.messages[0]["role"] == "system"
    # 最近消息保留
    assert "历史消息19" in agent.messages[-1]["content"]


def test_agent_compact_noop_when_short(tmp_path, qxt_home):
    """消息很短时 compact 不误伤 (系统提示 + 少量消息保持原样)。"""
    agent = _build_agent(tmp_path, qxt_home, [])
    agent.messages = [{"role": "system", "content": "sys"},
                      {"role": "user", "content": "hi"}]
    dropped = agent.compact()
    assert dropped == 0
    assert len(agent.messages) == 2


def test_estimate_cost_known_preset():
    """已知模型按定价表估算。"""
    # deepseek-chat: 0.00014 / 1k input, 0.00028 / 1k output
    cost = estimate_cost("deepseek", "deepseek-chat", 1000, 1000)
    assert abs(cost - (0.00014 + 0.00028)) < 1e-6


def test_estimate_cost_unknown_model_fallback():
    """未知模型用粗略默认价, 不抛异常。"""
    cost = estimate_cost("unknown", "mystery", 1000, 0)
    assert cost > 0


def test_cost_usage_accumulates_cache_fields(tmp_path, qxt_home):
    """total_usage 累计缓存命中/未命中, 供 /cost 展示。"""
    agent = _build_agent(tmp_path, qxt_home, [])
    agent._accumulate_usage({"prompt_tokens": 100, "completion_tokens": 50,
                             "prompt_cache_hit_tokens": 80,
                             "prompt_cache_miss_tokens": 20})
    assert agent.total_usage["prompt_tokens"] == 100
    assert agent.total_usage["prompt_cache_hit_tokens"] == 80
    assert agent.cache_hit_rate() == 0.8


def test_slash_compact_and_cost_handlers(tmp_path, qxt_home, capsys):
    """/compact 与 /cost 斜杠命令接线正确, 不抛异常。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home, [])
    config = agent.config
    agent.messages = [{"role": "system", "content": "sys"}]
    for i in range(20):
        agent.messages.append({"role": "user", "content": f"历史消息{i}" * 30})
    agent._accumulate_usage({"prompt_tokens": 1000, "completion_tokens": 500,
                             "prompt_cache_hit_tokens": 600,
                             "prompt_cache_miss_tokens": 400})

    assert _handle_slash("/compact", agent, config, str(tmp_path)) is True
    assert _handle_slash("/cost", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "已压缩" in out
    assert "估算成本" in out
