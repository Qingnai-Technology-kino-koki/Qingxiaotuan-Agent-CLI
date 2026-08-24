"""缓存前缀稳定 + 无感 compact + 缓存命中观测 测试 (Mock 模型, 离线)。

验证:
- build_system_prompt 不再接收 task_hint, 输出与用户输入无关 (逐字节稳定) → 可缓存;
- agent.run 首轮把语义召回(记忆/技能)贴进 user 消息而非 system, system 前缀不变;
- system_prompt_hash 在同会话多轮一致;
- ContextManager 超预算时迭代压缩直到回到预算内, 且 system 前缀不动;
- agent.cache_hit_rate 正确累计 DeepSeek 的 prompt_cache_hit_tokens;
- _extract_usage 解析缓存字段。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.core.prompts import build_system_prompt, build_task_context, system_prompt_hash
from qingxiaotuan.core.kernel import Kernel
from qingxiaotuan.app import build_kernel
from qingxiaotuan.context.manager import ContextManager, estimate_messages
from qingxiaotuan.models.openai_compat import _extract_usage
from qingxiaotuan.models.base import ModelAdapter, ModelResponse, ToolCall


def _echo_agent(tmp_path, qxt_home, usage_seq=None):
    """构造一个 Agent, 模型每轮回显 user 末段; 可选注入 usage 序列。"""
    kernel = build_kernel()
    config = kernel.require("config")
    class SeqModel(ModelAdapter):
        name = "seq"
        def __init__(self):
            self.i = 0
            self.usage_seq = list(usage_seq or [{}])
        def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
            self.i += 1
            u = self.usage_seq[(self.i - 1) % len(self.usage_seq)] if self.usage_seq else {}
            return ModelResponse(content="ok", usage=u)
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", SeqModel(), owner="test")
    return Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)


def test_build_system_prompt_has_no_task_hint_param_and_stable(tmp_path, qxt_home):
    import inspect
    sig = inspect.signature(build_system_prompt)
    assert "task_hint" not in sig.parameters, "system 提示不应再接收 task_hint (缓存杀手)"
    from qingxiaotuan.config.loader import Config
    cfg = Config()
    p1 = build_system_prompt(home=cfg.home, workspace=str(tmp_path))
    p2 = build_system_prompt(home=cfg.home, workspace=str(tmp_path))
    assert p1 == p2, "同一工作区 system 提示应逐字节一致"


def test_compact_keeps_tool_call_group_intact():
    from qingxiaotuan.context.manager import ContextManager

    messages = [{"role": "system", "content": "system"}]
    for index in range(3):
        messages.extend([
            {"role": "user", "content": f"task {index}"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": f"call-{index}", "function": {"name": "read_file", "arguments": "{}"}
            }]},
            {"role": "tool", "tool_call_id": f"call-{index}", "content": "ok"},
        ])
    manager = ContextManager(keep_recent=2, budget_tokens=1, strategy="none")
    compacted, dropped = manager.compact_if_needed(messages)
    assert dropped > 0
    for position, message in enumerate(compacted):
        if message.get("role") == "tool":
            assert position > 0
            previous = compacted[position - 1]
            assert previous.get("role") == "assistant"
            assert previous.get("tool_calls")


def test_agent_system_prefix_stable_across_turns(tmp_path, qxt_home):
    agent = _echo_agent(tmp_path, qxt_home)
    hashes = []
    for i in range(4):
        agent.run(f"任务 {i} 关于完全不同的主题", stream=False)
        hashes.append(system_prompt_hash(agent._system_prompt))
    # 同会话多轮 system 前缀哈希必须一致 (缓存命中前提)
    assert len(set(hashes)) == 1, f"system 前缀在多轮间变化: {hashes}"


def test_agent_system_prompt_equals_stable_builder_output(tmp_path, qxt_home):
    """核心缓存不变量: agent 的 system 提示 == build_system_prompt 输出,
    即 agent 没有把任何随任务变化的内容(用户输入/语义召回)塞进 system。"""
    kernel = build_kernel()
    config = kernel.require("config")
    store = kernel.get("memory_store")
    store.append_memory("用户偏好用 pytest 跑测试", section="偏好")
    class M(ModelAdapter):
        name = "m"
        def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
            return ModelResponse(content="ok")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", M(), owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)
    agent.run("怎么跑测试", stream=False)
    expected = build_system_prompt(
        home=config.home, workspace=str(tmp_path),
        memory_store=kernel.get("memory_store"),
        skill_manager=kernel.get("skill_manager") if config.get("skills.auto_inject", True) else None,
        skill_limit=config.get("skills.inject_limit", 3),
        codebase_map=agent._codebase_map,
    )
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[0]["content"] == expected, "system 提示不应含任何任务相关内容"


def test_build_task_context_injects_relevant_memory(tmp_path, qxt_home):
    """语义召回机制: 查询含已存记忆关键词时, build_task_context 应返回该记忆。"""
    kernel = build_kernel()
    store = kernel.get("memory_store")
    store.append_memory("用户偏好用 pytest 跑测试", section="偏好")
    # 用包含已存词 "pytest" 的查询, 触发 trigram FTS 匹配
    block = build_task_context("我们项目用 pytest 跑测试吗", memory_store=store)
    assert "pytest" in block, "相关记忆应被语义召回并注入任务上下文"


def test_context_manager_iterates_until_under_budget(tmp_path):
    # 造一堆中等长度消息, smart 模式下迭代压缩应能持续收缩
    msg = {"role": "user", "content": "x" * 200}  # ~50 token/条
    msgs = [{"role": "system", "content": "SYS"}]
    msgs += [dict(msg) for _ in range(200)]  # 200 条, 远超预算
    # 用会收缩的 summarize: 每次把 middle 压成更短的摘要
    def shrink(text: str) -> str:
        return "[摘要]" + text[:80]
    cm = ContextManager(keep_recent=10, budget_tokens=4000, compact_trigger=4000,
                        strategy="smart", summarize=shrink)
    out, dropped = cm.compact_if_needed(msgs)
    assert dropped > 0
    assert estimate_messages(out) <= cm.compact_trigger, "压缩后应回到预算内"
    assert out[0]["role"] == "system" and out[0]["content"] == "SYS", "system 前缀不动"
    # 幂等: 再压一次不应再掉内容
    out2, dropped2 = cm.compact_if_needed(out)
    assert dropped2 == 0


def test_cache_hit_rate_accumulates(tmp_path, qxt_home):
    agent = _echo_agent(tmp_path, qxt_home, usage_seq=[
        {"prompt_cache_hit_tokens": 1000, "prompt_cache_miss_tokens": 0},
        {"prompt_cache_hit_tokens": 2000, "prompt_cache_miss_tokens": 100},
    ])
    agent.run("a", stream=False)
    agent.run("b", stream=False)
    rate = agent.cache_hit_rate()
    assert rate is not None
    # (1000+2000) / (1000+2000+100) = 3000/3100
    assert abs(rate - (3000 / 3100)) < 1e-6


def test_extract_usage_parses_deepseek_cache_fields():
    class _U:
        prompt_tokens = 5000
        completion_tokens = 200
        prompt_cache_hit_tokens = 4800
        prompt_cache_miss_tokens = 200
    out = _extract_usage(_U())
    assert out["prompt_cache_hit_tokens"] == 4800
    assert out["prompt_cache_miss_tokens"] == 200
    assert out["prompt_tokens"] == 5000
