"""测试: 用量落盘与会话级聚合 —— 对标 Claude Code 的 /cost 跨会话统计。

覆盖:
- Agent._accumulate_usage 把每次调用的增量写入会话流 (usage 事件)
- SessionStore.sum_usage 聚合 delta/cost/模型分布
- load_messages 不受 usage 事件影响 (会话恢复兼容)
"""

from __future__ import annotations

import json
from pathlib import Path

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.memory.sessions import SessionStore


# ----------------------------------------------------------------- sum_usage 聚合

def test_sum_usage_aggregates_deltas_costs_models(tmp_path):
    store = SessionStore(tmp_path)
    store.append("usage", {"turn": 1, "delta": {"prompt_tokens": 100, "completion_tokens": 20},
                           "provider": "deepseek", "model": "deepseek-chat", "cost_usd": 0.02})
    store.append("usage", {"turn": 2, "delta": {"prompt_tokens": 50, "completion_tokens": 30,
                                                "prompt_cache_hit_tokens": 40},
                           "provider": "groq", "model": "llama-4", "cost_usd": None})

    summary = SessionStore.sum_usage(store.file)
    assert summary["prompt_tokens"] == 150
    assert summary["completion_tokens"] == 50
    assert summary["prompt_cache_hit_tokens"] == 40
    assert summary["calls"] == 2
    assert abs(summary["cost_usd"] - 0.02) < 1e-6   # cost_usd=None 的增量按 0 计
    assert summary["models"]["deepseek/deepseek-chat"] == 120
    assert summary["models"]["groq/llama-4"] == 120


def test_sum_usage_missing_or_empty(tmp_path):
    assert SessionStore.sum_usage(tmp_path / "nope.jsonl") == {}
    store = SessionStore(tmp_path)
    store.append("user", {"message": {"role": "user", "content": "hi"}})
    assert SessionStore.sum_usage(store.file) == {}


def test_load_messages_ignores_usage_events(tmp_path):
    store = SessionStore(tmp_path)
    store.append("user", {"message": {"role": "user", "content": "你好"}})
    store.append("usage", {"turn": 0, "delta": {"prompt_tokens": 10},
                           "provider": "x", "model": "y", "cost_usd": 0.0})
    msgs = SessionStore.load_messages(store.file)
    assert len(msgs) == 1 and msgs[0]["content"] == "你好"


# ----------------------------------------------------------------- Agent 集成

class _MockModel:
    name = "mock"

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        return None


def _build_agent_with_session(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", _MockModel(), owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda _p: True)
    return agent, kernel.get("session_store")


def test_agent_persists_usage_delta_to_session(tmp_path, qxt_home):
    """每次调用后 usage 增量事件落盘, 且带 provider/model/成本。"""
    agent, store = _build_agent_with_session(tmp_path, qxt_home)
    agent._accumulate_usage({"prompt_tokens": 200, "completion_tokens": 100})
    agent._accumulate_usage({"prompt_cache_hit_tokens": 60})   # 只有缓存字段也记录
    agent._accumulate_usage(None)                              # 空用量不产生事件

    events = [json.loads(line) for line in store.file.read_text(encoding="utf-8").splitlines()]
    usage_events = [e for e in events if e.get("type") == "usage"]
    assert len(usage_events) == 2
    first = usage_events[0]
    assert first["delta"] == {"prompt_tokens": 200, "completion_tokens": 100}
    assert first["turn"] == 0
    assert first["provider"] == agent.config.get("model.provider", "")
    assert isinstance(first["cost_usd"], (int, float))         # 定价表 fallback 也能估出成本

    summary = SessionStore.sum_usage(store.file)
    assert summary["prompt_tokens"] == 200
    assert summary["prompt_cache_hit_tokens"] == 60
    assert summary["calls"] == 2
