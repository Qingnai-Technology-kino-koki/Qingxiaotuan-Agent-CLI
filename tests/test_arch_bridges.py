"""测试 arch 层新增的桥接与加固功能:

- SemanticBus handler 异常隔离
- LoopProviderBridge 桥接 arch Loop -> core LoopProvider
- kernel_executor 工厂函数 (mock Agent)
- TieredContext._summarize 结构化压缩
"""
import pytest

from qingxiaotuan.arch.execution import (
    BaseAgentLoop,
    EventSemantics,
    LoopContext,
    LoopProviderBridge,
    LoopRegistry,
    LoopResult,
    SemanticBus,
    SemanticEvent,
)
from qingxiaotuan.arch.context import TieredContext


# ================================================================ SemanticBus 异常隔离


class TestSemanticBusExceptionIsolation:
    """handler 异常不应中断后续分发。"""

    def test_bad_handler_does_not_block_others(self):
        bus = SemanticBus()
        seen = []

        def good_handler(e):
            seen.append("good")

        def bad_handler(e):
            raise RuntimeError("boom")

        bus.subscribe(EventSemantics.EMIT, bad_handler)
        bus.subscribe(EventSemantics.EMIT, good_handler)

        event = SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={})
        result = bus.publish(event)

        # good_handler 应该被调用, 尽管 bad_handler 先抛异常
        assert seen == ["good"]
        # 事件仍然返回
        assert result.kind == EventSemantics.EMIT

    def test_wildcard_handler_exception_isolated(self):
        bus = SemanticBus()
        results = []

        def ok1(e):
            results.append("ok1")

        def bad(e):
            raise ValueError("fail")

        def ok2(e):
            results.append("ok2")

        bus.subscribe("*", ok1)
        bus.subscribe("*", bad)
        bus.subscribe("*", ok2)

        bus.publish(SemanticEvent(kind=EventSemantics.DECIDE, source="x", payload={}))
        assert results == ["ok1", "ok2"]

    def test_middleware_exception_is_tracked_and_isolated(self):
        """中间件异常被隔离并记录到 error_log, 不中断后续 handler。"""
        bus = SemanticBus()
        seen = []

        def bad_mw(e):
            raise RuntimeError("mw-fail")

        def good_handler(e):
            seen.append("ok")

        bus.add_middleware(bad_mw)
        bus.subscribe(EventSemantics.EMIT, good_handler)
        event = bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        # 中间件失败但 handler 仍然被调用
        assert seen == ["ok"]
        # 错误被记录
        assert bus.stats()["middleware_errors"] == 1


# ================================================================ LoopProviderBridge


class TestLoopProviderBridge:
    """LoopProviderBridge 把 arch BaseAgentLoop 桥接为 core LoopProvider。"""

    def test_bridge_name_and_description(self):
        class DummyLoop(BaseAgentLoop):
            name = "dummy"
            description = "A dummy loop"

            def run(self, ctx):
                return LoopResult(success=True, steps=0, final_answer="ok")

        bus = SemanticBus()
        loop = DummyLoop(bus)
        bridge = LoopProviderBridge(loop)

        assert bridge.name == "dummy"
        assert bridge.description == "A dummy loop"

    def test_bridge_implements_loop_provider_interface(self):
        """桥接对象应有 run_loop / on_task_start / on_task_end 方法。"""
        class NoopLoop(BaseAgentLoop):
            name = "noop"

            def run(self, ctx):
                return LoopResult(success=True, steps=0, final_answer="")

        bridge = LoopProviderBridge(NoopLoop())
        assert hasattr(bridge, "run_loop")
        assert callable(bridge.run_loop)
        assert hasattr(bridge, "on_task_start")
        assert hasattr(bridge, "on_task_end")

    def test_bridge_single_turn_finish(self):
        """简单场景: decide 第一步就 finish, 桥接应返回 answer。"""

        def simple_decide(call_idx):
            return {"action": "finish", "answer": "hello from bridge"}

        def simple_execute(name, args):
            return "ok"

        class SimpleLoop(BaseAgentLoop):
            name = "simple"

            def run(self, ctx):
                answer = ""
                history = []
                for step in range(ctx.max_steps):
                    d = ctx.decide_fn(step)
                    if d.get("action") == "finish" or d.get("finish"):
                        answer = d.get("answer", "")
                        break
                    obs = ctx.execute_fn(d.get("tool", ""), d.get("args", {}))
                    history.append(SemanticEvent(
                        kind=EventSemantics.OBSERVE, source="test",
                        payload={"observation": obs}))
                return LoopResult(success=True, steps=len(history), final_answer=answer)

        bridge = LoopProviderBridge(SimpleLoop())

        # 构造最小 agent mock
        agent = _make_mock_agent(
            messages=[{"role": "user", "content": "hi"}],
            decide_fn=simple_decide,
            execute_fn=simple_execute,
        )
        agent.config._data["agent.max_iterations"] = 5

        answer = bridge.run_loop(agent, "hi", stream=False, max_iterations=5)
        assert answer == "hello from bridge"

    def test_bridge_tool_then_finish(self):
        """两轮: 第一轮执行工具, 第二轮 finish。

        桥接的 _execute 调用 registry.dispatch(), 不是 agent._execute_fn,
        所以用 on_tool 回调验证工具调用发生。
        """
        tools_called = []

        def on_tool(name, args_str):
            tools_called.append(name)

        # FakeAgent._chat_with_retry 用 _call_count:
        #   idx=0 -> tool call (返回 tool_calls)
        #   idx=1 -> finish (返回 content, 无 tool_calls)
        def fake_decide(call_idx):
            if call_idx == 0:
                return {"action": "tool", "tool": "read_file", "args": '{"path":"x.py"}'}
            return {"action": "finish", "answer": "done after tool"}

        class TwoStepLoop(BaseAgentLoop):
            name = "two-step"

            def run(self, ctx):
                history = []
                answer = ""
                for step in range(ctx.max_steps):
                    d = ctx.decide_fn(step)
                    if d.get("action") == "finish":
                        answer = d.get("answer", "")
                        break
                    obs = ctx.execute_fn(d.get("tool", ""), d.get("args", {}))
                    history.append(SemanticEvent(
                        kind=EventSemantics.OBSERVE, source="test",
                        payload={"observation": obs}))
                return LoopResult(success=True, steps=len(history), final_answer=answer)

        bridge = LoopProviderBridge(TwoStepLoop())

        agent = _make_mock_agent(
            messages=[{"role": "user", "content": "read it"}],
            decide_fn=fake_decide,
            execute_fn=lambda n, a: "ok",
        )
        agent.config._data["agent.max_iterations"] = 10

        answer = bridge.run_loop(agent, "read it", stream=False,
                                on_tool=on_tool, max_iterations=10)
        assert answer == "done after tool"
        # on_tool 回调应记录 read_file 工具调用
        assert "read_file" in tools_called

    def test_bridge_on_tool_callback(self):
        """on_tool 回调应被触发。"""
        tool_calls_seen = []

        def cb_decide(call_idx):
            if call_idx == 0:
                return {"action": "tool", "tool": "ls", "args": "{}"}
            return {"action": "finish", "answer": "ok"}

        def noop_execute(name, args):
            return "output"

        class CallbackLoop(BaseAgentLoop):
            name = "cb"

            def run(self, ctx):
                history = []
                answer = ""
                for step in range(ctx.max_steps):
                    d = ctx.decide_fn(step)
                    if d.get("action") == "finish":
                        answer = d.get("answer", "")
                        break
                    obs = ctx.execute_fn(d.get("tool", ""), d.get("args", {}))
                    history.append(SemanticEvent(
                        kind=EventSemantics.OBSERVE, source="test",
                        payload={"observation": obs}))
                return LoopResult(success=True, steps=len(history), final_answer=answer)

        bridge = LoopProviderBridge(CallbackLoop())

        def on_tool(name, args_str):
            tool_calls_seen.append(name)

        agent = _make_mock_agent(
            messages=[],
            decide_fn=cb_decide,
            execute_fn=noop_execute,
        )
        agent.config._data["agent.max_iterations"] = 5

        bridge.run_loop(agent, "go", stream=False, on_tool=on_tool, max_iterations=5)
        assert "ls" in tool_calls_seen

    def test_bridge_stats_tracks_errors(self):
        """bridge.stats() 应返回运行统计。"""
        class FailLoop(BaseAgentLoop):
            name = "fail"
            def run(self, ctx):
                # 模拟: decide_fn 抛异常
                try:
                    ctx.decide_fn(0)
                except Exception:
                    pass
                return LoopResult(success=True, steps=0, final_answer="err")

        bridge = LoopProviderBridge(FailLoop())
        agent = _make_mock_agent(messages=[], decide_fn=lambda i: (_ for _ in ()).throw(RuntimeError()), execute_fn=lambda n, a: "ok")
        agent.config._data["agent.max_iterations"] = 3

        bridge.run_loop(agent, "go", stream=False, max_iterations=3)
        stats = bridge.stats()
        assert stats["loop_name"] == "fail"
        assert stats["error_count"] >= 1
        assert len(stats["last_errors"]) >= 1


# ================================================================ kernel_executor (mock)


class TestKernelExecutor:
    """kernel_executor 工厂测试。"""

    def test_returns_callable_with_correct_signature(self):
        """kernel_executor 返回一个可调用对象, 签名为 (goal, AgentContext, Path) -> (str, dict)。"""
        from qingxiaotuan.arch.orchestration import kernel_executor, AgentContext
        from pathlib import Path
        import inspect

        mock_kernel = _make_mock_kernel()
        executor = kernel_executor(mock_kernel, max_iterations=3)

        # executor 应是 callable
        assert callable(executor)
        # 签名: (goal: str, ctx: AgentContext, worktree: Path) -> (str, dict)
        sig = inspect.signature(executor)
        params = list(sig.parameters.keys())
        assert params == ["goal", "ctx", "worktree"]


# ================================================================ TieredContext._summarize 改进


class TestTieredContextSummarize:
    """测试 _summarize 的结构化压缩策略。"""

    def test_preserves_system_message(self):
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="system", content="You are a coding agent. Be concise."),
            ContextEntry(role="user", content="hello world"),
        ]
        summary = TieredContext._summarize(entries)
        assert "[SYS]" in summary
        assert "coding agent" in summary

    def test_extracts_user_first_and_last_sentence(self):
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="user", content="First sentence. Middle stuff here. Last important point."),
        ]
        summary = TieredContext._summarize(entries)
        assert "[USR]" in summary
        assert "First sentence" in summary
        assert "Last important point" in summary

    def test_tool_messages_only_keep_status(self):
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="tool", content="已写入 file.py: 42 lines modified"),
            ContextEntry(role="tool", content="[错误] read_file: file not found"),
            ContextEntry(role="tool", content="[超时] web_fetch in 120s"),
        ]
        summary = TieredContext._summarize(entries)
        assert "[TOOLS]" in summary
        # 应包含状态标记 (ok/err/timeout)
        assert "(ok)" in summary
        assert "(err)" in summary
        assert "(timeout)" in summary
        # 工具内容截断到 30 字符 ("已写入 file.py: 42 lines modified" 约 26 字符, 完整保留)
        # 但关键状态已标注, 不含完整返回体如 "file not found" 的详细堆栈
        assert "read_file" in summary

    def test_summary_stays_under_800_chars(self):
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="system", content="x" * 500),
            ContextEntry(role="user", content="y" * 500),
            ContextEntry(role="assistant", content="z" * 500),
        ] + [
            ContextEntry(role="tool", content=f"tool-{i} result " + "a" * 200)
            for i in range(20)
        ]
        summary = TieredContext._summarize(entries)
        assert len(summary) <= 800

    def test_deduplicates_tool_names(self):
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="tool", content="已写入 a.py"),
            ContextEntry(role="tool", content="已写入 a.py"),
            ContextEntry(role="tool", content="已写入 b.py"),
        ]
        summary = TieredContext._summarize(entries)
        # 应去重, a.py 只出现一次在 TOOLS 行
        tools_part = summary.split("[TOOLS]")[-1] if "[TOOLS]" in summary else ""
        assert tools_part.count("a.py") <= 1

    def test_compaction_uses_improved_summarize(self):
        """端到端: TieredContext 压缩后 warm 块应是结构化摘要。"""
        ctx = TieredContext(budget_tokens=500, hot_capacity=3)
        ctx.anchor_system("You are a helpful assistant.")
        # 灌入足够消息触发压缩
        for i in range(10):
            ctx.add("user", f"Question {i}: what is {i} + {i}?")
        assert ctx.compactions >= 1
        # 最后一个 warm 块 (非锚点) 应该是结构化摘要
        warm_summaries = [e for e in ctx.warm if e.content.startswith("[compressed")]
        assert len(warm_summaries) >= 1
        last_summary = warm_summaries[-1].content
        assert "[USR]" in last_summary or "[TOOLS]" in last_summary or "[SYS]" in last_summary


# ================================================================ helpers


class _FakeConfig:
    """极简 Config mock, 支持 config.get() 和 _data 字典。"""

    def __init__(self):
        self._data = {
            "agent.max_iterations": 20,
            "agent.skill_nudge_interval": 3,
        }

    def get(self, key, default=None):
        return self._data.get(key, default)


class _FakeRegistry:
    """极简 registry mock: schemas 返回空, dispatch 按名返回。"""

    def schemas(self, tool_set=None, exclude_tools=None):
        return []

    def dispatch(self, name, args, ctx):
        return f"dispatched:{name}"


class _FakeCtx:
    """极简 ToolContext mock。"""
    ledger = None
    hooks = None


class _FakeModel:
    """极简 model mock。"""
    capabilities = None


class _FakeToolCall:
    """模拟 ModelResponse.tool_calls 元素 (需有 id/name/arguments 属性)。"""
    def __init__(self, id="call-0", name="", arguments="{}"):
        self.id = id
        self.name = name
        self.arguments = arguments


class _FakeResult:
    """模拟 ModelResponse。"""
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = None


class _FakeAgent:
    """最小 Agent mock, 供 LoopProviderBridge 测试使用。

    _call_count 跟踪 _chat_with_retry 调用次数, 让 decide_fn 能根据轮次返回不同决策。
    """

    def __init__(self, messages=None, decide_fn=None, execute_fn=None):
        self.messages = messages or []
        self.turn_count = 0
        self.config = _FakeConfig()
        self.registry = _FakeRegistry()
        self.ctx = _FakeCtx()
        self.model = _FakeModel()
        self.exclude_tools = set()
        self._cancel_event = type("_E", (), {"is_set": lambda self: False})()
        self._call_count = 0

        # inject mock decide/execute
        self._decide_fn = decide_fn
        self._execute_fn = execute_fn

    def effective_tool_set(self):
        return set()

    def _chat_with_retry(self, messages, tools=None, stream=True,
                         on_token=None, on_reason=None):
        """模拟模型调用: 根据 decide_fn 生成响应。

        decide_fn 签名: (call_index: int) -> decision_dict
        call_index 从 0 开始, 每次 _chat_with_retry 调用 +1。
        """
        if self._decide_fn is None:
            return None
        idx = self._call_count
        self._call_count += 1
        d = self._decide_fn(idx)
        if d.get("action") == "finish":
            return _FakeResult(content=d.get("answer", ""))
        # 有 tool call
        tc = _FakeToolCall(
            id=f"call-{idx}",
            name=d.get("tool", ""),
            arguments=d.get("args", "{}"),
        )
        return _FakeResult(content="", tool_calls=[tc])

    def _session_append(self, kind, **kw):
        pass

    def _estimate_total_cost(self):
        return 0.0


def _make_mock_agent(messages=None, decide_fn=None, execute_fn=None):
    return _FakeAgent(messages=messages, decide_fn=decide_fn, execute_fn=execute_fn)


class _FakeKernel:
    """极简 kernel mock: require 返回预注册的服务。"""

    def __init__(self):
        self._services = {}

    def provide(self, name, obj, owner=None):
        self._services[name] = obj

    def require(self, name):
        if name not in self._services:
            raise KeyError(f"service {name!r} not registered")
        return self._services[name]

    def get(self, name, default=None):
        # Agent.__init__ 现通过 kernel.get 读取可选服务 (session_store 等)
        return self._services.get(name, default)


def _make_mock_kernel():
    """创建一个预注册了 loop_provider / model_adapter / tool_registry 的 mock kernel。"""
    kernel = _FakeKernel()

    # 注册一个 mock loop provider
    class _MockLoopProvider:
        name = "mock-react"
        description = "Mock loop for testing"
        should_nudge = staticmethod(lambda agent, interval=3: False)

        def run_loop(self, agent, user_input, *, stream=True, on_token=None,
                     on_tool=None, on_reason=None, on_tool_result=None,
                     on_error=None, max_iterations=None, session_id=None):
            return f"mock-result: {user_input}"

        def on_task_start(self, agent, user_input):
            pass

        def on_task_end(self, agent, answer, iteration):
            pass

    kernel.provide("loop_provider", _MockLoopProvider())

    # 注册 config
    kernel.provide("config", _FakeConfig())

    # Agent.__init__ 需要 model_adapter 和 tool_registry
    class _MockModelAdapter:
        """极简 model adapter: chat 返回空响应。"""
        capabilities = type("C", (), {"vision": False})()

        def chat(self, messages, **kw):
            return None

    kernel.provide("model_adapter", _MockModelAdapter())

    class _MockToolRegistry:
        """极简工具注册表。"""
        def schemas(self, **kw):
            return []
        def dispatch(self, name, args, ctx):
            return f"mock-dispatch:{name}"

    kernel.provide("tool_registry", _MockToolRegistry())

    # Agent.__init__ 可能需要 session_store / codebase_indexer 等
    class _MockSessionStore:
        """极简 session store。"""
        def append(self, *a, **kw):
            pass
        session_id = "test-session"

    kernel.provide("session_store", _MockSessionStore())

    # codebase_indexer 是可选的
    kernel.provide("codebase_indexer", None)

    return kernel


# ================================================================ SemanticBus 新功能

class TestSemanticBusStatsAndErrors:
    """测试 SemanticBus 的统计和错误收集。"""

    def test_stats_tracks_published_count(self):
        bus = SemanticBus()
        assert bus.stats()["published"] == 0
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        bus.publish(SemanticEvent(kind=EventSemantics.OBSERVE, source="t", payload={}))
        assert bus.stats()["published"] == 2

    def test_stats_tracks_handler_errors(self):
        bus = SemanticBus()

        def bad(e):
            raise RuntimeError("fail")

        bus.subscribe(EventSemantics.EMIT, bad)
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        stats = bus.stats()
        assert stats["handler_errors"] == 1
        assert stats["published"] == 1

    def test_recent_errors_returns_most_recent(self):
        bus = SemanticBus()

        def bad(e):
            raise RuntimeError("oops")

        bus.subscribe("*", bad)
        for i in range(5):
            bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={"i": i}))
        errors = bus.recent_errors(limit=3)
        assert len(errors) == 3
        # 每条错误应包含 handler/event_kind/error/ts
        assert all("error" in e and "ts" in e for e in errors)

    def test_clear_errors(self):
        bus = SemanticBus()
        bus.subscribe(EventSemantics.EMIT, lambda e: (_ for _ in ()).throw(RuntimeError()))
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert bus.stats()["handler_errors"] == 1
        bus.clear_errors()
        assert bus.stats()["handler_errors"] == 0

    def test_unsubscribe(self):
        bus = SemanticBus()
        seen = []
        handler = lambda e: seen.append(1)
        bus.subscribe(EventSemantics.EMIT, handler)
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert seen == [1]
        assert bus.unsubscribe(EventSemantics.EMIT, handler)
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert seen == [1]  # 不再触发

    def test_middleware_error_is_tracked(self):
        bus = SemanticBus()

        def bad_mw(e):
            raise ValueError("mw-fail")

        bus.add_middleware(bad_mw)
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert bus.stats()["middleware_errors"] == 1

    def test_subscriber_kinds_tracked(self):
        bus = SemanticBus()
        bus.subscribe(EventSemantics.EMIT, lambda e: None)
        bus.subscribe(EventSemantics.OBSERVE, lambda e: None)
        bus.subscribe("*", lambda e: None)
        kinds = bus.stats()["subscriber_kinds"]
        assert "emit" in kinds
        assert "observe" in kinds
        assert "*" in kinds


# ================================================================ TieredContext._summarize 新功能

class TestTieredContextSummarizeV2:
    """测试 _summarize 的扩展状态推断和代码块处理。"""

    def test_new_tool_statuses(self):
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="tool", content="[已限流] 工具 web_fetch 速率限制"),
            ContextEntry(role="tool", content="[已禁用] 工具 run_shell 已被排除"),
            ContextEntry(role="tool", content="[安全拦截] MCP 工具危险操作"),
            ContextEntry(role="tool", content="[cached] read_file 缓存命中"),
        ]
        summary = TieredContext._summarize(entries)
        assert "(rate-limited)" in summary
        assert "(disabled)" in summary
        assert "(blocked)" in summary
        assert "(cached)" in summary

    def test_code_block_handled(self):
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="assistant", content="```python\ndef hello():\n    print('hi')\n```"),
        ]
        summary = TieredContext._summarize(entries)
        assert "(code" in summary
        # 不应包含完整代码
        assert "print('hi')" not in summary

    def test_code_block_with_pre_text(self):
        """包含代码块的混合内容: 检测到 ``` 就按代码块处理。"""
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="assistant", content="Here is the fix:\n```python\nx = 1\n```"),
        ]
        summary = TieredContext._summarize(entries)
        # 包含代码块, 应提取说明文本
        assert "Here is the fix" in summary

    def test_benign_tool_is_ok(self):
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="tool", content="已写入 file.py: 10 lines"),
            ContextEntry(role="tool", content="搜索结果: 3 个匹配"),
        ]
        summary = TieredContext._summarize(entries)
        # 两个都应该是 ok
        assert summary.count("(ok)") == 2

    def test_tool_dedup_by_name_prefix(self):
        """同一工具不同状态应只保留首次出现 (按工具名前缀去重)。"""
        from qingxiaotuan.arch.context import ContextEntry
        entries = [
            ContextEntry(role="tool", content="read_file 结果 A"),
            ContextEntry(role="tool", content="[错误] read_file 失败"),
            ContextEntry(role="tool", content="read_file 结果 C"),
            ContextEntry(role="tool", content="write_file 已写入"),
        ]
        summary = TieredContext._summarize(entries)
        # read_file 只出现一次, write_file 出现一次
        assert summary.count("read_file") == 1
        assert summary.count("write_file") == 1


# ================================================================ SemanticBus 高级功能

class TestSemanticBusAdvanced:
    """测试 SemanticBus 的错误上限、快照、清除等功能。"""

    def test_error_accumulation_limit(self):
        """错误积累不应超过 _MAX_ERRORS。"""
        bus = SemanticBus()

        def bad(e):
            raise RuntimeError("fail")

        bus.subscribe(EventSemantics.EMIT, bad)
        # 发布超过上限的事件
        for _ in range(300):
            bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert bus.stats()["handler_errors"] <= bus._MAX_ERRORS

    def test_history_limit(self):
        """历史记录不应超过 _MAX_HISTORY。"""
        bus = SemanticBus()
        bus.subscribe(EventSemantics.EMIT, lambda e: None)
        for _ in range(1200):
            bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert len(bus.history()) <= bus._MAX_HISTORY

    def test_stats_snapshot(self):
        """stats_snapshot 应返回原子快照。"""
        bus = SemanticBus()
        bus.subscribe(EventSemantics.EMIT, lambda e: None)
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        snap = bus.stats_snapshot()
        assert "handler_call_counts" in snap
        assert isinstance(snap["handler_call_counts"], dict)

    def test_subscribe_count(self):
        """subscribe_count 应返回指定语义的订阅者数量。"""
        bus = SemanticBus()
        bus.subscribe(EventSemantics.EMIT, lambda e: None)
        bus.subscribe(EventSemantics.EMIT, lambda e: None)
        bus.subscribe(EventSemantics.OBSERVE, lambda e: None)
        assert bus.subscribe_count(EventSemantics.EMIT) == 2
        assert bus.subscribe_count(EventSemantics.OBSERVE) == 1
        assert bus.subscribe_count(EventSemantics.DECIDE) == 0

    def test_clear_history(self):
        """clear_history 应清空历史但不影响统计。"""
        bus = SemanticBus()
        bus.subscribe(EventSemantics.EMIT, lambda e: None)
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        assert bus.stats()["published"] == 1
        bus.clear_history()
        assert bus.stats()["history_size"] == 0
        assert bus.stats()["published"] == 1  # 计数不受影响

    def test_handler_call_counts(self):
        """handler_call_counts 应跟踪每个 handler 的调用次数。"""
        bus = SemanticBus()

        def h1(e):
            pass

        def h2(e):
            pass

        bus.subscribe(EventSemantics.EMIT, h1)
        bus.subscribe(EventSemantics.EMIT, h2)
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        bus.publish(SemanticEvent(kind=EventSemantics.EMIT, source="t", payload={}))
        counts = bus.stats()["handler_call_counts"]
        assert counts.get("h1") == 2
        assert counts.get("h2") == 2
