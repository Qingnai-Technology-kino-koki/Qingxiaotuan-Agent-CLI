# -*- coding: utf-8 -*-
"""OpenTelemetry 观测桥单测 —— 覆盖零依赖回退、payload 构建、span/counter 采集。"""
import asyncio

import pytest

from qingxiaotuan.ports.telemetry.otel import (
    OtlpTransport,
    build_otlp_metrics_payload,
    build_otlp_traces_payload,
    end_span,
    otel_available,
    record_counter,
    record_span,
    snapshot_counters,
)
from qingxiaotuan.ports.telemetry.types import EnrichedTelemetryEvent


def _ev(name="agent.run"):
    return EnrichedTelemetryEvent(
        event=name, timestamp=1_700_000_000.0,
        properties={"model": "x", "n": 3},
        context={"app_name": "t"},
    )


# ---------------------------------------------------------------- 无依赖可用性
def test_module_importable_without_opentelemetry():
    # 未装 otel 可选依赖也必须可导入, 且 otel_available 返回 False (不抛)
    from qingxiaotuan.ports.telemetry import otel  # noqa: F401
    assert isinstance(otel_available(), bool)


def test_end_span_empty_stack_noop():
    # 空 span 栈 end_span 不抛异常
    end_span()


# ---------------------------------------------------------------- payload 构建
def test_build_otlp_traces_payload():
    payload = build_otlp_traces_payload([_ev("agent.run"), _ev("agent.run")])
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 2
    assert spans[0]["name"] == "agent.run"
    # 事件属性 + 上下文都被映射成 attribute
    keys = {a["key"] for a in spans[0]["attributes"]}
    assert {"model", "n", "app_name"} <= keys


def test_build_otlp_metrics_payload():
    payload = build_otlp_metrics_payload({"tasks": 5})
    sums = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    assert sums[0]["name"] == "tasks"
    assert sums[0]["sum"]["value"] == 5
    assert sums[0]["sum"]["isMonotonic"] is True


# ---------------------------------------------------------------- transport 回退
def test_otlp_transport_send_falls_back_to_disk(tmp_path, monkeypatch):
    # 0 依赖 (otel_available=False) + POST 失败 -> 落盘 JSONL, 不抛
    monkeypatch.setattr("qingxiaotuan.ports.telemetry.otel.otel_available", lambda: False)
    monkeypatch.setattr(
        "qingxiaotuan.ports.telemetry.otel._post_json", lambda url, payload: False)
    out = tmp_path / "otel.jsonl"
    t = OtlpTransport(endpoint="http://collector.invalid/v1/traces",
                      fallback_path=str(out))
    asyncio.run(t.send([_ev()]))
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "agent.run" in lines[0]


def test_otlp_transport_noop_on_empty(monkeypatch):
    monkeypatch.setattr("qingxiaotuan.ports.telemetry.otel.otel_available", lambda: False)
    monkeypatch.setattr("qingxiaotuan.ports.telemetry.otel._post_json",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    t = OtlpTransport()
    asyncio.run(t.send([]))  # 空事件不应触发任何网络/落盘


def test_otlp_transport_uses_sdk_when_available(monkeypatch, tmp_path):
    # 当 otel_available()==True 时, send() 走真实 SDK 分支 _send_via_sdk
    called = {}
    def fake_sdk(self, events):
        called["events"] = len(events)
    monkeypatch.setattr("qingxiaotuan.ports.telemetry.otel.otel_available", lambda: True)
    monkeypatch.setattr(OtlpTransport, "_send_via_sdk", fake_sdk)
    t = OtlpTransport(fallback_path=str(tmp_path / "otel.jsonl"))
    asyncio.run(t.send([_ev()]))
    assert called.get("events") == 1


# ---------------------------------------------------------------- span / counter
def test_record_counter_and_snapshot():
    record_counter("tasks", 2)
    record_counter("tasks", 3)
    record_counter("other", 1)
    snap = snapshot_counters()
    assert snap["tasks"] == 5
    assert snap["other"] == 1


def test_span_nesting_stack():
    record_span("outer")
    record_span("inner")
    end_span()
    from qingxiaotuan.ports.telemetry.otel import _current_stack
    assert [s.name for s in _current_stack()] == ["outer"]
    end_span()
    assert _current_stack() == []


# ---------------------------------------------------------------- trace_id 继承
def test_event_trace_id_inherited_from_context():
    # 事件上下文携带 trace_id 时, span payload 应继承该 trace, 而非随机/全 0
    ev = EnrichedTelemetryEvent(
        event="agent.run", timestamp=1_700_000_000.0,
        properties={}, context={"trace_id": "abcd" * 8, "span_id": "11" * 8},
    )
    payload = build_otlp_traces_payload([ev])
    span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["traceId"] == "abcd" * 8
    assert span["spanId"] == "11" * 8


def test_batch_trace_id_override():
    # 显式传入 trace_id 时整批共享同一 trace
    evs = [_ev("a"), _ev("b")]
    payload = build_otlp_traces_payload(evs, trace_id="f" * 32)
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert {s["traceId"] for s in spans} == {"f" * 32}


# ---------------------------------------------------------------- 采样
def test_sampling_rate_one_keeps_all():
    t = OtlpTransport(sample_rate=1.0)
    evs = [_ev("agent.run"), _ev("tool.read")]
    assert len(t._should_sample(evs)) == 2


def test_sampling_rate_zero_keeps_none():
    t = OtlpTransport(sample_rate=0.0)
    assert t._should_sample([_ev("agent.run")]) == []


def test_sampling_is_deterministic():
    t = OtlpTransport(sample_rate=0.5)
    evs = [_ev("agent.run"), _ev("tool.read")]
    first = [e.event for e in t._should_sample(evs)]
    second = [e.event for e in t._should_sample(evs)]
    assert first == second


# ---------------------------------------------------------------- provider / close
def test_close_idempotent_without_sdk():
    # 未装 SDK 时 provider 为 None, close 应幂等不抛
    t = OtlpTransport()
    assert t._provider is None
    t.close()
    t.close()


def test_send_metrics_noop_when_no_counters():
    t = OtlpTransport()
    assert asyncio.run(t.send_metrics()) is False