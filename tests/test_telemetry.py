"""TelemetryCollector 单元测试: span 上下文管理器、指标聚合、并发安全。"""

import threading

from qingxiaotuan.core.telemetry import TelemetryCollector


def _collector_with_spans(tmp_path) -> TelemetryCollector:
    tc = TelemetryCollector(tmp_path)
    trace_id = tc.start_trace("agent.run")
    with tc.span("tool.read", trace_id=trace_id) as child:
        child.add_event("read", {"n": 2})
    tc.finish_trace(trace_id)
    return tc


def test_export_to_otlp_propagates_trace_and_span_ids(tmp_path):
    """export_to_otlp 应把 span 转成事件, 并携带规范化后的 trace/span/parent id。"""
    tc = _collector_with_spans(tmp_path)
    captured = {"events": []}

    class FakeTransport:
        async def send(self, events):
            captured["events"].extend(events)

    n = tc.export_to_otlp(FakeTransport())
    assert n == 1
    assert len(captured["events"]) == 1
    ev = captured["events"][0]
    assert ev.event == "tool.read"
    assert ev.context["trace_id"] == tc.get_recent_spans(1)[0]["trace_id"].replace("-", "")
    assert ev.properties["status"] == "ok"
    assert ev.properties["duration_ms"] >= 0


def test_export_to_otlp_noop_when_no_spans(tmp_path):
    tc = TelemetryCollector(tmp_path)

    class FakeTransport:
        async def send(self, events):
            raise AssertionError("不应发送空事件")

    assert tc.export_to_otlp(FakeTransport()) == 0


def test_span_context_manager_ok(tmp_path):
    """span() 正常退出应 finish 并标记为 ok。"""
    tc = TelemetryCollector(tmp_path)
    trace_id = tc.start_trace("t")
    with tc.span("step", trace_id=trace_id) as s:
        assert s.is_active
    assert not s.is_active
    assert s.status == "ok"
    stats = tc.get_stats()
    assert stats["total_spans"] == 1


def test_span_context_manager_error(tmp_path):
    """span() 抛异常应 finish 并标记为 error, 且异常照常传播。"""
    tc = TelemetryCollector(tmp_path)
    trace_id = tc.start_trace("t")
    s = None
    try:
        with tc.span("boom", trace_id=trace_id) as sp:
            s = sp
            raise ValueError("x")
    except ValueError:
        pass
    assert s is not None
    assert not s.is_active
    assert s.status == "error"
    assert tc.get_stats()["total_errors"] == 1


def test_model_and_tool_metrics(tmp_path):
    """record_model_call / record_tool_call 正确累加。"""
    tc = TelemetryCollector(tmp_path)
    tc.record_model_call(100.5, 150, success=True)
    tc.record_model_call(50.0, 0, success=False)
    tc.record_tool_call("read_file", 12.3, success=True)
    tc.record_tool_call("run_shell", 999.0, success=False)
    stats = tc.get_stats()
    assert stats["total_tokens"] == 150
    assert stats["total_tool_calls"] == 2
    assert stats["avg_tool_latency_ms"] == (12.3 + 999.0) / 2
    assert stats["avg_model_latency_ms"] == (100.5 + 50.0) / 2


def test_concurrent_tool_recording_is_safe(tmp_path):
    """多线程并发 record_tool_call 不丢数据也不抛异常 (收集器需线程安全)。"""
    tc = TelemetryCollector(tmp_path)

    def worker(i):
        for _ in range(50):
            tc.record_tool_call(f"tool_{i}", 1.0, success=True)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats = tc.get_stats()
    assert stats["total_tool_calls"] == 8 * 50


def test_get_stats_no_active_spans(tmp_path):
    """无 span 时 get_stats 不抛错, active_spans 为 0。"""
    tc = TelemetryCollector(tmp_path)
    stats = tc.get_stats()
    assert stats["active_spans"] == 0
    assert stats["total_spans"] == 0


def test_export_jsonl_writes_spans(tmp_path):
    """export_jsonl 把 span 持久化为 JSONL。"""
    tc = TelemetryCollector(tmp_path)
    trace_id = tc.start_trace("t")
    with tc.span("m", trace_id=trace_id):
        pass
    n = tc.export_jsonl()
    assert n == 1


def test_tool_executor_records_spans_and_metrics(tmp_path):
    """ToolExecutor 在 telemetry 传入后, 每个工具调用生成 span 并记录延迟/成败。"""
    from qingxiaotuan.core.tool_executor import ToolExecutor

    tc = TelemetryCollector(tmp_path)

    class FakeRegistry:
        def dispatch(self, name, args, ctx):
            return f"result of {name}"

    msgs: list = []
    ex = ToolExecutor(
        registry=FakeRegistry(), messages=msgs,
        telemetry=tc, trace_id="trace-1",
    )
    # 串行写工具
    ex.execute_batch([{"function": {"name": "write_file", "arguments": "{}"}}], ctx=None)
    # 并行只读工具 (两条)
    ex.execute_batch([
        {"function": {"name": "read_file", "arguments": "{}"}},
        {"function": {"name": "glob", "arguments": "{}"}},
    ], ctx=None)

    stats = tc.get_stats()
    assert stats["total_tool_calls"] == 3
    names = [s["name"] for s in tc.get_recent_spans(10)]
    assert "tool.write_file" in names
    assert "tool.read_file" in names
    assert "tool.glob" in names
    # 工具结果已写入 messages
    assert len(msgs) == 3
    assert all(m["status"] == "ok" for m in msgs)


def test_tool_executor_no_telemetry_overhead(tmp_path):
    """未传入 telemetry 时 ToolExecutor 行为不变、不产生任何遥测 (零开销)。"""
    from qingxiaotuan.core.tool_executor import ToolExecutor

    class FakeRegistry:
        def dispatch(self, name, args, ctx):
            return "ok"

    msgs: list = []
    ex = ToolExecutor(registry=FakeRegistry(), messages=msgs)
    assert ex._telemetry is None
    ex.execute_batch([{"function": {"name": "read_file", "arguments": "{}"}}], ctx=None)
    assert len(msgs) == 1
