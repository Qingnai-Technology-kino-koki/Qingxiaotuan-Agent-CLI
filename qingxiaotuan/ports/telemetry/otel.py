# -*- coding: utf-8 -*-
"""OpenTelemetry 观测桥 (评审 Major #5)。

现状: ``ports/telemetry`` 只有统一 event 追踪 (TelemetryClient / EventSink),
没有 trace (span) 与 metric 采集, 也没有标准后端导出。本模块补上:

- ``OtlpTransport``: 一个 ``TelemetryTransport`` 实现, 把 buffered 的
  ``EnrichedTelemetryEvent`` 转成 OTLP 资源 span, 投递到标准后端 (OTLP/HTTP
  collector)。这样现有 ``EventSink`` 只需换一个 transport 即可得到可观测导出。
- span/metric 便捷采集: ``record_span`` (traces) 与 ``record_counter`` (metrics),
  带线程局部 span 栈, 支持嵌套。
- 双模式:
  1) 安装了可选依赖 ``".[otel]"`` (opentelemetry-api/sdk/exporter) →
     使用真实 OpenTelemetry SDK 的 OTLP/HTTP exporter;
  2) 未安装 → 零依赖回退, 用 stdlib ``urllib`` 直接 POST OTLP/HTTP JSON
     (标准 OTLP collector 即可接收), 失败降级写 JSONL 文件。

本模块保证无副作用 import: 未安装 opentelemetry 时 ``otel_available()`` 为 False,
各入口自动走回退分支, 不抛 ImportError。与 crypto extras 的回退策略一致。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from .transport import TelemetryTransport
from .types import EnrichedTelemetryEvent

log = logging.getLogger(__name__)

_DEFAULT_OTLP_ENDPOINT = "http://localhost:4318/v1/traces"
_SERVICE_NAME = "qingxiaotuan"


def otel_available() -> bool:
    """是否安装了可选 OpenTelemetry SDK (启用真实 OTLP exporter 模式)。"""
    try:
        import opentelemetry.sdk  # noqa: F401
        import opentelemetry.exporter.otlp.proto.http.trace_exporter  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ OTLP payload 构建

def _event_to_span_json(ev: EnrichedTelemetryEvent, trace_id: Optional[str] = None) -> Dict[str, Any]:
    """把一条遥测事件转成 OTLP/HTTP 的 span JSON (简化 proto-json 子集)。

    ``trace_id`` 来自调用方给定的批次 trace; 若空, 则回落到事件自带上下文里的
    ``trace_id``/``traceId`` (由上游 TelemetryCollector 写入), 再退化为随机生成,
    确保一批事件能共享同一 trace, 便于在 OTLP 后端聚合。
    """
    ts_ns = int(ev.timestamp * 1_000_000_000) if ev.timestamp else time.time_ns()
    resolved_trace = trace_id or ev.context.get("trace_id") or ev.context.get("traceId")
    if not resolved_trace:
        resolved_trace = _hex32()
    attrs = [{"key": k, "value": {"stringValue": _as_str(v)}} for k, v in ev.properties.items()]
    for k, v in ev.context.items():
        if k in ("trace_id", "traceId", "span_id", "spanId"):
            continue
        attrs.append({"key": k, "value": {"stringValue": _as_str(v)}})
    return {
        "traceId": str(resolved_trace),
        "spanId": ev.context.get("span_id") or ev.context.get("spanId") or _hex16(),
        "parentSpanId": _parent_span_id(ev),
        "name": ev.event,
        "kind": 3,  # SPAN_KIND_INTERNAL
        "startTimeUnixNano": str(ts_ns),
        "endTimeUnixNano": str(ts_ns),
        "attributes": attrs,
    }


def _parent_span_id(ev: EnrichedTelemetryEvent) -> Optional[str]:
    """若事件上下文携带 parent span id, 则保留层级关系; 否则省略 parent。"""
    pid = ev.context.get("parent_span_id") or ev.context.get("parentSpanId")
    return str(pid) if pid else None


def _hex32() -> str:
    return format(int.from_bytes(os.urandom(16), "big"), "032x")


def _as_str(v: Any) -> str:
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def _hex16() -> str:
    return format(int.from_bytes(os.urandom(8), "big"), "016x")


def build_otlp_traces_payload(
    events: Sequence[EnrichedTelemetryEvent],
    service_name: str = _SERVICE_NAME,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """按 OTLP/HTTP 协议 (proto-json) 把事件打包为 resourceSpans。

    ``trace_id`` 可显式指定以聚合整批事件到同一 trace; 默认逐事件回落。
    """
    scope_events = [_event_to_span_json(ev, trace_id=trace_id or None) for ev in events]
    resource_attrs = [
        {"key": "service.name", "value": {"stringValue": service_name}},
        {"key": "telemetry.sdk.language", "value": {"stringValue": "python"}},
        {"key": "telemetry.sdk.name", "value": {"stringValue": "opentelemetry"}},
    ]
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [
                    {
                        "scope": {"name": "qingxiaotuan"},
                        "spans": scope_events,
                    }
                ],
            }
        ]
    }


def build_otlp_metrics_payload(
    counters: Dict[str, int],
    service_name: str = _SERVICE_NAME,
) -> Dict[str, Any]:
    """把增量计数器聚合为 Sum 指标 (metric export 便捷出口)。"""
    sums = [
        {
            "name": name,
            "sum": {"value": int(val), "aggregationTemporality": 2, "isMonotonic": True},
        }
        for name, val in counters.items()
    ]
    resource_attrs = [{"key": "service.name", "value": {"stringValue": service_name}}]
    return {
        "resourceMetrics": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeMetrics": [{"metrics": sums}],
            }
        ]
    }


# ------------------------------------------------------------------ 零依赖 OTLP/HTTP 回退

def _post_json(url: str, payload: Dict[str, Any]) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - 显式用户配置端点
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001
        log.warning("OTLP export failed: %s", exc)
        return False


class OtlpTransport(TelemetryTransport):
    """把遥测事件导出到标准 OTLP/HTTP 后端的传输实现。

    安装了 ``".[otel]"`` 时用真实 OpenTelemetry exporter; 否则用 stdlib urllib
    直接 POST OTLP/HTTP JSON。导入与构造均无副作用。
    """

    def __init__(self, endpoint: Optional[str] = None,
                 service_name: str = _SERVICE_NAME,
                 fallback_path: Optional[str] = None,
                 sample_rate: float = 1.0) -> None:
        self.endpoint = endpoint or os.environ.get(
            "QXT_OTEL_TRACES_ENDPOINT", _DEFAULT_OTLP_ENDPOINT)
        self.service_name = service_name
        self.fallback_path = fallback_path
        self.sample_rate = min(1.0, max(0.0, float(sample_rate)))
        self._provider: Any = None
        self._sdk_lock = threading.Lock()

    def _should_sample(self, events: Sequence[EnrichedTelemetryEvent]) -> List[EnrichedTelemetryEvent]:
        """按采样率确定性过滤事件 (同一事件名稳定命中), 降流量。"""
        if self.sample_rate >= 1.0 or not events:
            return list(events)
        kept = []
        for ev in events:
            key = str(ev.event)
            # 确定性哈希 → 稳定采样, 避免重复事件忽采忽不采
            h = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
            if (h % 10000) / 10000.0 < self.sample_rate:
                kept.append(ev)
        return kept

    async def send(self, events: Sequence[EnrichedTelemetryEvent], signal: Any = None) -> None:
        if not events:
            return
        sampled = self._should_sample(events)
        if not sampled:
            return
        try:
            if otel_available():
                self._send_via_sdk(sampled)
            else:
                payload = build_otlp_traces_payload(sampled, self.service_name)
                if not _post_json(self.endpoint, payload):
                    self.save_to_disk(sampled)
        except Exception as exc:  # noqa: BLE001
            log.warning("OTLP send failed, falling back to disk: %s", exc)
            self.save_to_disk(sampled)

    def _send_via_sdk(self, events: Sequence[EnrichedTelemetryEvent]) -> None:
        # 惰性导入 + 缓存 provider/exporter, 避免每次发送都重建连接
        provider = self._get_provider()
        tracer = provider.get_tracer(self.service_name)
        for ev in events:
            with tracer.start_as_current_span(ev.event) as span:
                for k, v in ev.properties.items():
                    span.set_attribute(k, _as_str(v))

    def _get_provider(self) -> Any:
        if self._provider is not None:
            return self._provider
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        with self._sdk_lock:
            if self._provider is None:
                provider = TracerProvider()
                provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(
                    endpoint=self.endpoint,
                )))
                self._provider = provider
        return self._provider

    def close(self) -> None:
        """关闭并冲刷底层 SDK tracer (幂等)。"""
        provider = self._provider
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._provider = None

    def __del__(self) -> None:  # 兜底: 尽力关闭底层面板
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    async def send_metrics(self) -> bool:
        """把当前进程计数器快照导出为 OTLP Metrics (Sum), 成功返回 True。"""
        counters = snapshot_counters()
        if not counters:
            return False
        payload = build_otlp_metrics_payload(counters, self.service_name)
        endpoint = self.endpoint.replace("/v1/traces", "/v1/metrics")
        try:
            if otel_available():
                return self._send_metrics_via_sdk(counters)
            return _post_json(endpoint, payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("OTLP metrics export failed: %s", exc)
            return False

    def _send_metrics_via_sdk(self, counters: Dict[str, int]) -> bool:
        from opentelemetry.metrics import get_meter
        meter = get_meter(self.service_name)
        for name, val in counters.items():
            meter.create_counter(name, unit="1", description="").add(int(val))
        return True

    def save_to_disk(self, events: Sequence[EnrichedTelemetryEvent]) -> None:
        if not events or not self.fallback_path:
            return
        try:
            with open(self.fallback_path, "a", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps({
                        "event": ev.event,
                        "ts": ev.timestamp,
                        "properties": ev.properties,
                        "context": ev.context,
                    }) + "\n")
        except Exception:  # noqa: BLE001
            pass

    async def retry_disk_events(self) -> None:
        return


# ------------------------------------------------------------------ span / counter 便捷采集

class Span:
    __slots__ = ("name", "start", "attributes")

    def __init__(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.name = name
        self.start = time.time()
        self.attributes: Dict[str, Any] = dict(attributes or {})


_tls = threading.local()


def _current_stack() -> List[Span]:
    if not hasattr(_tls, "spans"):
        _tls.spans = []
    return _tls.spans


def record_span(name: str, attributes: Optional[Dict[str, Any]] = None) -> Span:
    """开启并压入一个 span (traces), 支持嵌套; 与事件无关, 纯本地计时。"""
    span = Span(name, attributes)
    _current_stack().append(span)
    return span


def end_span() -> None:
    """弹栈结束最近 span; 在安装了 otel 时向 OTLP 投递该 span (尽力而为)。"""
    stack = _current_stack()
    if not stack:
        return
    span = stack.pop()
    if otel_available():
        try:
            _emit_span(span)
        except Exception:  # noqa: BLE001
            pass


def _emit_span(span: Span) -> None:
    from opentelemetry import trace
    from opentelemetry.trace import SpanContext, TraceFlags, SpanKind
    # 通过真实 SDK 的 noop/活跃 tracer 生成一个 span 落地到已配置 exporter
    tracer = trace.get_tracer(_SERVICE_NAME)
    with tracer.start_as_current_span(span.name, kind=SpanKind.INTERNAL) as sp:
        for k, v in span.attributes.items():
            sp.set_attribute(k, _as_str(v))


_counters: Dict[str, int] = {}
_counters_lock = threading.Lock()


def record_counter(name: str, value: int = 1) -> None:
    """累计进程内计数器 (metrics), 供批量化导出到 OTLP Metrics。"""
    with _counters_lock:
        _counters[name] = _counters.get(name, 0) + int(value)


def snapshot_counters() -> Dict[str, int]:
    with _counters_lock:
        return dict(_counters)