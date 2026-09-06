"""预算/费用报告修复的回归测试。

修复前:
- commands.py 读 agent.total_usage['_cost_usd'] (该键不存在) -> 永远 $0.00
- commands.py 读 agent._session_cost (从未维护) -> 费用行永远不显示
两者现在统一改用 agent._estimate_total_cost() (从 token 用量估算真实花费)。
"""

import types
from pathlib import Path

import pytest

from qingxiaotuan.core import agent as agent_mod


def test_estimate_total_cost_uses_token_usage():
    # 鸭子类型 Agent: 仅需 config.get 与 total_usage
    class Cfg:
        def get(self, k, d=None):
            return d

    fake = types.SimpleNamespace(
        config=Cfg(),
        total_usage={"prompt_tokens": 1000, "completion_tokens": 500},
    )
    fn = agent_mod.Agent._estimate_total_cost.__get__(fake)

    import qingxiaotuan.models.router as router

    orig = router.estimate_cost
    router.estimate_cost = lambda p, m, pt, ct: pt * 0.001 + ct * 0.002
    try:
        val = fn()
    finally:
        router.estimate_cost = orig

    assert isinstance(val, float)
    assert val == pytest.approx(1000 * 0.001 + 500 * 0.002)


def test_commands_no_longer_reads_dead_keys():
    # 文档化: 成本报告源码 (已随 CLI 拆分迁到 cmd_chat) 不再引用
    # 不存在的 _cost_usd / _session_cost, 统一改用 agent._estimate_total_cost()
    src = (Path(agent_mod.__file__).parents[1] / "cli" / "cmd_chat.py").read_text(
        encoding="utf-8"
    )
    assert "_cost_usd" not in src, "cmd_chat.py 不应再读不存在的 _cost_usd 键"
    assert "_session_cost" not in src, "cmd_chat.py 不应再读未维护的 _session_cost"
    assert "_estimate_total_cost()" in src, "应改用 _estimate_total_cost()"
