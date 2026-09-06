"""执行层测试: 5 种事件语义 + 可插拔 Loop + 工具管线 waterfall。"""
import pytest

from qingxiaotuan.arch.execution import (
    EventSemantics,
    LoopContext,
    LoopRegistry,
    ReActLoop,
    SemanticBus,
    ToolCall,
    ToolPipeline,
)


def test_event_semantics_five():
    kinds = {e.value for e in EventSemantics}
    assert kinds == {"emit", "observe", "decide", "reflect", "terminate"}


def test_semantic_bus_routes_by_kind():
    bus = SemanticBus()
    seen = []
    bus.subscribe(EventSemantics.EMIT, lambda e: seen.append(e.kind))
    bus.publish(__import__("qingxiaotuan.arch.execution", fromlist=["SemanticEvent"]).SemanticEvent(
        kind=EventSemantics.EMIT, source="t", payload={}))
    bus.publish(__import__("qingxiaotuan.arch.execution", fromlist=["SemanticEvent"]).SemanticEvent(
        kind=EventSemantics.OBSERVE, source="t", payload={}))
    assert [str(k) for k in seen] == ["emit"]


def test_loop_registry_pluggable():
    before = set(LoopRegistry.names())
    # ReAct 已注册
    assert "react" in before
    from qingxiaotuan.arch.execution import BaseAgentLoop

    class MyLoop(BaseAgentLoop):
        name = "custom-x"

        def run(self, ctx):
            return __import__("qingxiaotuan.arch.execution", fromlist=["LoopResult"]).LoopResult(
                success=True, steps=0, final_answer="x")

    LoopRegistry.register("custom-x", MyLoop)
    assert "custom-x" in LoopRegistry.names()
    inst = LoopRegistry.get("custom-x")(SemanticBus())
    assert inst.name == "custom-x"


def test_react_loop_emits_semantics():
    bus = SemanticBus()
    kinds = []
    for k in EventSemantics:
        bus.subscribe(k, lambda e, k=k: kinds.append(str(k)))

    def decide(history):
        # 第一步就 finish
        return {"action": "finish", "answer": "done"}

    ctx = LoopContext(goal="g", decide_fn=decide, execute_fn=lambda n, a: "ok")
    res = ReActLoop(bus).run(ctx)
    assert res.success
    assert res.final_answer == "done"
    # 至少应出现 decide + terminate
    assert "decide" in kinds and "terminate" in kinds


def test_react_loop_full_cycle():
    bus = SemanticBus()
    calls = []

    def decide(history):
        if not history:
            return {"action": "tool", "tool": "search", "args": {"q": 1}}
        return {"action": "finish", "answer": "found it"}

    def execute(name, args):
        calls.append(name)
        return "result-for-" + name

    ctx = LoopContext(goal="g", decide_fn=decide, execute_fn=execute)
    res = ReActLoop(bus).run(ctx)
    assert res.final_answer == "found it"
    assert "search" in calls
    assert res.steps == 1  # 一次 observe


def test_tool_pipeline_waterfall_gates_and_audits():
    bus = SemanticBus()
    observed = []
    bus.subscribe(EventSemantics.OBSERVE, lambda e: observed.append(e.payload))

    calls = []
    pipeline = ToolPipeline(bus=bus)

    def pre(stage_call, ctx):
        calls.append("pre")
        return stage_call

    def post(stage_call, outcome, ctx):
        calls.append("post")
        return outcome

    pipeline.add_pre_stage(pre)
    pipeline.add_post_stage(post)

    ctx = LoopContext(goal="g", execute_fn=lambda n, a: f"ran {n}")

    # gate 拒绝
    def deny_gate(c):
        return (False, "not allowed")

    ctx.gate_fn = deny_gate
    out = pipeline.run(ToolCall(name="rm", args={}), ctx)
    assert out.ok is False
    assert "gate-denied" in out.error
    assert not observed  # 被拒不应产生 observe

    # gate 通过
    ctx.gate_fn = lambda c: (True, "ok")
    out2 = pipeline.run(ToolCall(name="ls", args={}), ctx)
    assert out2.ok is True
    assert "pre" in calls and "post" in calls
    assert observed and observed[0]["tool"] == "ls"
