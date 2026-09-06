"""编排层测试: 5 子代理并发 + 3 层嵌套 + 冲突解决 + 人类审批。"""
import pytest

from qingxiaotuan.arch.orchestration import (
    AgentContext,
    AgentSpec,
    ConflictResolver,
    HumanApprovalGate,
    Orchestrator,
    Resolution,
)


def _shared_executor(goal, ctx: AgentContext, worktree):
    # 写到共享资源, 不同代理内容不同 -> 触发冲突
    (worktree / "shared.txt").write_text(f"agent={ctx.agent_id}:{goal}\n", encoding="utf-8")
    return f"ok:{goal}", {"shared.txt": f"agent={ctx.agent_id}:{goal}"}


def test_five_agents_concurrent():
    specs = [AgentSpec(id=f"a{i}", goal=f"task{i}") for i in range(5)]
    orch = Orchestrator(executor=_shared_executor)
    res = orch.spawn(specs)
    assert len(res.results) == 5
    assert all(r.error is None for r in res.results)


def test_three_layer_nesting():
    # 顶层 a0, 派 2 个嵌套 (depth1), 每个再派 2 个 (depth2) -> 共 3 层
    specs = [
        AgentSpec(
            id="a0", goal="root",
            children=[
                AgentSpec(id="a0-1", goal="c1",
                          children=[AgentSpec(id="a0-1-1", goal="g"), AgentSpec(id="a0-1-2", goal="g")]),
                AgentSpec(id="a0-2", goal="c2",
                          children=[AgentSpec(id="a0-2-1", goal="g"), AgentSpec(id="a0-2-2", goal="g")]),
            ],
        )
    ]
    orch = Orchestrator(executor=_shared_executor)
    res = orch.spawn(specs)
    root = res.results[0]
    assert len(root.children) == 2
    assert len(root.children[0].children) == 2
    # depth: a0-1-1 的 depth 应为 2 (0-indexed, 3 层)
    assert root.children[0].children[0].depth == 2


def test_conflict_detection_and_resolution():
    # 两个代理写同一 shared.txt 不同内容 -> 冲突
    specs = [AgentSpec(id="x", goal="X"), AgentSpec(id="y", goal="Y")]
    resolver = ConflictResolver(strategy="last-writer-wins", priority={"y": 10, "x": 1})
    orch = Orchestrator(executor=_shared_executor, resolver=resolver)
    res = orch.spawn(specs)
    assert len(res.conflicts) == 1
    assert res.conflicts[0].resource == "shared.txt"
    # y 优先级高 -> 胜出, 内容含 agent=y
    rid, resolution, content = res.resolutions[0]
    assert resolution == Resolution.LAST_WRITER_WINS
    assert content is not None and "agent=y" in content


def test_human_approval_gate_modes():
    gate_auto = HumanApprovalGate("auto")
    gate_deny = HumanApprovalGate("deny")
    gate_cb = HumanApprovalGate("callback", callback=lambda p: p.get("winner") == "y")
    prop = {"winner": "y"}
    assert gate_auto.request(prop) is True
    assert gate_deny.request(prop) is False
    assert gate_cb.request(prop) is True
    assert gate_cb.request({"winner": "x"}) is False


def test_escalate_uses_gate():
    specs = [AgentSpec(id="x", goal="X"), AgentSpec(id="y", goal="Y")]
    gate = HumanApprovalGate("deny")
    resolver = ConflictResolver(strategy="escalate", gate=gate, priority={"y": 1, "x": 0})
    orch = Orchestrator(executor=_shared_executor, resolver=resolver)
    res = orch.spawn(specs)
    rid, resolution, content = res.resolutions[0]
    assert resolution == Resolution.REJECTED
    assert content is None


def test_subagent_cleans_temp_worktree():
    """SubAgent.run() 完成后应清理临时工作树。"""
    import os
    specs = [AgentSpec(id="cleanup-test", goal="test")]
    orch = Orchestrator(executor=_shared_executor)
    res = orch.spawn(specs)
    assert len(res.results) == 1
    wt_path = res.results[0].worktree
    assert wt_path is not None
    # 工作树应已被清理
    assert not os.path.exists(wt_path), f"临时工作树 {wt_path} 未被清理"


def test_orchestrator_timeout_skips_slow_agents():
    """超时的子代理应被跳过并记录错误。"""
    import time

    def slow_executor(goal, ctx, worktree):
        time.sleep(10)  # 模拟慢子代理
        return "done", {}

    specs = [AgentSpec(id="slow", goal="slow-task")]
    orch = Orchestrator(executor=slow_executor, per_agent_timeout=0.1)
    res = orch.spawn(specs)
    assert len(res.results) == 1
    assert res.results[0].error is not None
    assert "Timeout" in res.results[0].error
