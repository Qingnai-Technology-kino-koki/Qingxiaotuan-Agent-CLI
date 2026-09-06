"""arch 层安全修复回归测试。

验证:
- ToolPipeline._audit 在 gate 拦截时发布 DECIDE 语义事件 (不只是 OBSERVE)
- ToolPipeline 在 pre stage 异常时正确 short-circuit
- SemanticBus 中间件短路行为
"""

import pytest

from qingxiaotuan.arch.execution import (
    EventSemantics,
    LoopContext,
    SemanticBus,
    SemanticEvent,
    ToolCall,
    ToolOutcome,
    ToolPipeline,
)


class TestToolPipelineGateAudit:
    """gate 拦截时应产生 DECIDE 语义事件, 便于审计追踪。"""

    def test_gate_denied_produces_decide_event(self):
        bus = SemanticBus()
        events = []
        bus.subscribe("*", lambda e: events.append(e))

        pipeline = ToolPipeline(bus=bus)
        ctx = LoopContext(goal="test", gate_fn=lambda c: (False, "not allowed"))
        outcome = pipeline.run(ToolCall(name="rm", args={"path": "/"}), ctx)

        assert outcome.ok is False
        assert "gate-denied" in outcome.error
        # 应有 DECIDE 事件记录安全拦截
        decide_events = [e for e in events if e.kind == EventSemantics.DECIDE]
        assert len(decide_events) >= 1
        assert decide_events[-1].payload["action"] == "blocked"
        assert "not allowed" in decide_events[-1].payload["reason"]
        # 不应有 OBSERVE (工具未真正执行)
        observe_events = [e for e in events if e.kind == EventSemantics.OBSERVE]
        assert len(observe_events) == 0

    def test_gate_allowed_produces_observe_event(self):
        bus = SemanticBus()
        events = []
        bus.subscribe("*", lambda e: events.append(e))

        pipeline = ToolPipeline(bus=bus)
        ctx = LoopContext(goal="test", execute_fn=lambda n, a: "ok",
                          gate_fn=lambda c: (True, "ok"))
        outcome = pipeline.run(ToolCall(name="ls", args={}), ctx)

        assert outcome.ok is True
        # 应有 OBSERVE 事件
        observe_events = [e for e in events if e.kind == EventSemantics.OBSERVE]
        assert len(observe_events) >= 1
        assert observe_events[-1].payload["tool"] == "ls"

    def test_hook_block_produces_decide_event(self):
        bus = SemanticBus()
        events = []
        bus.subscribe("*", lambda e: events.append(e))

        class FakeHooks:
            def run_pre(self, kind, data):
                raise PermissionError("hook blocked")

        pipeline = ToolPipeline(bus=bus, hooks=FakeHooks())
        ctx = LoopContext(goal="test")
        outcome = pipeline.run(ToolCall(name="write", args={}), ctx)

        assert outcome.ok is False
        assert "hook-pre-blocked" in outcome.error
        decide_events = [e for e in events if e.kind == EventSemantics.DECIDE]
        assert len(decide_events) >= 1
        assert decide_events[-1].payload["action"] == "blocked"


class TestToolPipelinePreStageException:
    """pre stage 异常应 short-circuit 并记录到审计。"""

    def test_pre_stage_exception_short_circuits(self):
        bus = SemanticBus()
        events = []
        bus.subscribe("*", lambda e: events.append(e))

        pipeline = ToolPipeline(bus=bus)

        def bad_pre(call, ctx):
            raise RuntimeError("pre-failed")

        pipeline.add_pre_stage(bad_pre)
        ctx = LoopContext(goal="test", execute_fn=lambda n, a: "ok")
        outcome = pipeline.run(ToolCall(name="ls", args={}), ctx)

        assert outcome.ok is False
        assert "pre-failed" in outcome.error
        # 有 DECIDE 事件记录拦截
        decide_events = [e for e in events if e.kind == EventSemantics.DECIDE]
        assert len(decide_events) >= 1


class TestSemanticBusMiddlewareChain:
    """中间件链在返回 None 时应阻止事件传播。"""

    def test_middleware_returns_none_blocks_handler(self):
        bus = SemanticBus()
        seen = []

        def blocking_mw(e):
            return None  # 阻止传播

        bus.add_middleware(blocking_mw)
        bus.subscribe(EventSemantics.EMIT, lambda e: seen.append("called"))

        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert seen == []  # handler 未被调用

    def test_middleware_modifies_payload(self):
        bus = SemanticBus()
        seen = []

        def adding_mw(e):
            e.payload["injected"] = True
            return e

        bus.add_middleware(adding_mw)
        bus.subscribe(EventSemantics.EMIT, lambda e: seen.append(e.payload))

        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert seen[0]["injected"] is True
