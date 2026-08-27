"""自动模型路由策略 (core/auto_route) 单元测试 —— 纯逻辑, 不依赖网络/模型。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qingxiaotuan.core.auto_route import AutoRouter, RouteSession, tool_messages_of_turn


def _cfg(**over):
    base = {
        "router.enabled": True,
        "router.auto_switch": True,
        "router.plan_execute": False,
        "router.escalate_on_stuck": True,
        "router.stuck_threshold": 3,
    }
    base.update(over)
    return base


def _tool(status):
    return {"role": "tool", "content": {"status": status, "content": "x"}}


def test_observe_resets_on_success():
    a = AutoRouter(_cfg())
    s = RouteSession()
    s.stuck = 2
    a.observe_turn(s, [_tool("ok"), _tool("cached")])
    assert s.stuck == 0


def test_observe_increments_on_error():
    a = AutoRouter(_cfg())
    s = RouteSession()
    a.observe_turn(s, [_tool("error")])
    assert s.stuck == 1
    a.observe_turn(s, [_tool("error")])
    assert s.stuck == 2


def test_observe_no_tools_does_not_change_stuck():
    a = AutoRouter(_cfg())
    s = RouteSession()
    s.stuck = 1
    a.observe_turn(s, [])  # 纯文本回复轮
    assert s.stuck == 1


def test_plan_execute_first_turn_strong_rest_cheap():
    a = AutoRouter(_cfg(**{"router.plan_execute": True}))
    s = RouteSession()
    assert a.decide_override(s) == 10      # turn0 规划: 强
    a.observe_turn(s, [_tool("ok")])       # → turn1
    assert a.decide_override(s) == 2       # 执行: 便宜
    a.observe_turn(s, [_tool("ok")])       # → turn2
    assert a.decide_override(s) == 2


def test_escalate_after_threshold_then_back_to_cheap():
    a = AutoRouter(_cfg(**{"router.plan_execute": True, "router.stuck_threshold": 2}))
    s = RouteSession()
    assert a.decide_override(s) == 10      # turn0 规划: 强
    a.observe_turn(s, [_tool("error")])    # → turn1, stuck=1
    assert a.decide_override(s) == 2       # 还没到阈值: 便宜
    a.observe_turn(s, [_tool("error")])    # → turn2, stuck=2 达到阈值
    assert a.decide_override(s) == 10      # 升级救场 (escalated_until=2)
    a.observe_turn(s, [_tool("ok")])       # → turn3, stuck 清零
    assert a.decide_override(s) == 2       # 救场窗口结束, 降回便宜


def test_no_plan_execute_returns_zero():
    a = AutoRouter(_cfg(plan_execute=False))
    s = RouteSession()
    assert a.decide_override(s) == 0       # 不强制, 交回 router.decide


def test_tool_messages_of_turn_slices():
    msgs = [{"role": "user"}, {"role": "assistant"}, {"role": "tool"}, {"role": "tool"}]
    assert len(tool_messages_of_turn(msgs, 2)) == 2
    assert tool_messages_of_turn(msgs, 5) == []  # 越界安全
