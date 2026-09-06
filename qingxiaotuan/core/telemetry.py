"""OpenTelemetry-compatible Telemetry — 可观测性 (对标 Claude Code 2.1.239 Telemetry)。

功能:
- Span 树: 每次 Agent 循环、工具调用、模型请求都有独立的 span
- Metrics: token 用量、工具调用延迟、错误率、上下文占用
- Export: JSON Lines 格式, 可被外部工具消费
- /stats 增强: 活跃 span、最近 N 分钟的性能摘要

实现:
- 纯 Python, 不依赖 opentelemetry-sdk (可选接入)
- 本地 JSONL 文件导出 (QXT_HOME/telemetry/)
- 内存环形缓冲 (最近 1000 条事件)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


def _hex(value: Any) -> str:
    """把任意 id 规范化为十六进制字符串 (用于 OTLP trace/span id)。

    容忍含连字符的 UUID / 含下划线的自定义 id; 无法解析时退化为新生成的 hex。
    """
    if value is None:
        return uuid.uuid4().hex
    cleaned = "".join(ch for ch in str(value).lower() if ch in "0123456789abcdef")
    return cleaned or uuid.uuid4().hex


# ================================================================ Span

@dataclass
class Span:
    """Telemetry span — 一次操作的可观测单元。"""
    span_id: str
    trace_id: str
    name: str
    start_time: float
    end_time: float = 0.0
    status: str = "ok"             # ok | error | cancelled
    parent_id: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000

    @property
    def is_active(self) -> bool:
        return self.end_time == 0.0

    def finish(self, status: str = "ok") -> None:
        self.end_time = time.time()
        self.status = status

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "start_time": self.start_time,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
        }
        if self.parent_id:
            d["parent_id"] = self.parent_id
        if self.attributes:
            d["attributes"] = self.attributes
        if self.events:
            d["events"] = self.events
        return d


# ================================================================ Telemetry Collector

class TelemetryCollector:
    """Telemetry 收集器 — 管理 span 树和指标。

    用法:
        tc = TelemetryCollector(Path("~/.qingxiaotuan"))
        # 开始一个 trace
        trace_id = tc.start_trace("agent.run")
        # 在 trace 内创建 span
        span = tc.start_span("model.chat", trace_id=trace_id)
        # ... 执行操作 ...
        span.finish()
        # 结束 trace
        tc.finish_trace(trace_id)
        # 获取统计
        stats = tc.get_stats()
    """

    def __init__(self, home: Path, max_spans: int = 1000) -> None:
        self.home = Path(home)
        self._telemetry_dir = self.home / "telemetry"
        self._telemetry_dir.mkdir(parents=True, exist_ok=True)
        self._max_spans = max_spans
        self._spans: deque[Span] = deque(maxlen=max_spans)
        self._active_spans: Dict[str, Span] = {}
        self._traces: Dict[str, Dict[str, Any]] = {}
        self._metrics: Dict[str, Any] = {
            "total_traces": 0,
            "total_spans": 0,
            "total_errors": 0,
            "total_tokens": 0,
            "total_tool_calls": 0,
            "tool_call_latencies": deque(maxlen=100),
            "model_call_latencies": deque(maxlen=100),
        }
        # 工具调用在 ThreadPoolExecutor 内并发执行, 收集器的可变状态需加锁保护。
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def span(self, name: str, trace_id: str = "", parent_id: Optional[str] = None,
             attributes: Optional[Dict[str, Any]] = None):
        """上下文管理器: 进入时开启 span, 退出时 (无论正常/异常) 自动关闭。"""
        s = self.start_span(name, trace_id=trace_id, parent_id=parent_id, attributes=attributes)
        try:
            yield s
        except Exception:  # noqa: BLE001
            self.finish_span(s.span_id, status="error")
            raise
        else:
            self.finish_span(s.span_id, status="ok")

    def start_trace(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> str:
        """开始一个新的 trace。"""
        with self._lock:
            trace_id = str(uuid.uuid4())
            self._traces[trace_id] = {
                "name": name,
                "start_time": time.time(),
                "attributes": attributes or {},
                "spans": [],
            }
            self._metrics["total_traces"] += 1
        return trace_id

    def finish_trace(self, trace_id: str, status: str = "ok") -> None:
        """结束 trace。"""
        with self._lock:
            if trace_id in self._traces:
                self._traces[trace_id]["end_time"] = time.time()
                self._traces[trace_id]["status"] = status

    def start_span(
        self,
        name: str,
        trace_id: str = "",
        parent_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        """开始一个新的 span。"""
        with self._lock:
            span = Span(
                span_id=str(uuid.uuid4()),
                trace_id=trace_id or "",
                name=name,
                start_time=time.time(),
                parent_id=parent_id,
                attributes=attributes or {},
            )
            self._active_spans[span.span_id] = span
            self._spans.append(span)
            self._metrics["total_spans"] += 1

            # 关联到 trace
            if trace_id and trace_id in self._traces:
                self._traces[trace_id]["spans"].append(span.span_id)
        return span

    def finish_span(self, span_id: str, status: str = "ok") -> Optional[Span]:
        """结束 span。达到自动导出阈值时触发 auto_export 防内存泄漏。"""
        should_export = False
        with self._lock:
            span = self._active_spans.pop(span_id, None)
            if span:
                span.finish(status)
                if status == "error":
                    self._metrics["total_errors"] += 1
                # 检查是否需要自动导出 (锁内检查, 锁外执行)
                should_export = len(self._spans) >= self._AUTO_EXPORT_THRESHOLD
        if should_export:
            self.auto_export()
        return span

    def record_tool_call(self, tool_name: str, latency_ms: float, success: bool) -> None:
        """记录工具调用指标。"""
        with self._lock:
            self._metrics["total_tool_calls"] += 1
            self._metrics["tool_call_latencies"].append({
                "tool": tool_name,
                "latency_ms": latency_ms,
                "success": success,
                "timestamp": time.time(),
            })

    def record_model_call(self, latency_ms: float, tokens: int, success: bool) -> None:
        """记录模型调用指标。"""
        with self._lock:
            self._metrics["model_call_latencies"].append({
                "latency_ms": latency_ms,
                "tokens": tokens,
                "success": success,
                "timestamp": time.time(),
            })
            self._metrics["total_tokens"] += tokens

    def add_span_event(self, span_id: str, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """向活跃 span 添加事件。"""
        with self._lock:
            span = self._active_spans.get(span_id)
            if span:
                span.add_event(name, attributes)

    def get_stats(self) -> Dict[str, Any]:
        """获取统计摘要。"""
        with self._lock:
            active_spans = [s.to_dict() for s in self._active_spans.values()]
            recent_tool_calls = list(self._metrics["tool_call_latencies"])[-20:]
            recent_model_calls = list(self._metrics["model_call_latencies"])[-20:]

            avg_tool_latency = 0.0
            if recent_tool_calls:
                avg_tool_latency = sum(t["latency_ms"] for t in recent_tool_calls) / len(recent_tool_calls)

            avg_model_latency = 0.0
            if recent_model_calls:
                avg_model_latency = sum(t["latency_ms"] for t in recent_model_calls) / len(recent_model_calls)

            return {
                "total_traces": self._metrics["total_traces"],
                "total_spans": self._metrics["total_spans"],
                "total_errors": self._metrics["total_errors"],
                "total_tokens": self._metrics["total_tokens"],
                "total_tool_calls": self._metrics["total_tool_calls"],
                "active_spans": len(active_spans),
                "avg_tool_latency_ms": round(avg_tool_latency, 2),
                "avg_model_latency_ms": round(avg_model_latency, 2),
                "error_rate": round(
                    self._metrics["total_errors"] / max(self._metrics["total_spans"], 1) * 100, 2
                ),
                "recent_tool_calls": recent_tool_calls,
                "recent_model_calls": recent_model_calls,
            }

    def export_jsonl(self, path: Optional[Path] = None) -> int:
        """导出所有 span 为 JSONL 格式。"""
        export_path = path or self._telemetry_dir / f"spans_{int(time.time())}.jsonl"
        count = 0
        try:
            with open(export_path, "w", encoding="utf-8") as f:
                for span in self._spans:
                    f.write(json.dumps(span.to_dict(), ensure_ascii=False) + "\n")
                    count += 1
        except Exception as exc:
            log.debug("Telemetry export failed: %s", exc)
        return count

    def get_recent_spans(self, n: int = 20) -> List[Dict[str, Any]]:
        """获取最近 N 条 span。"""
        return [s.to_dict() for s in list(self._spans)[-n:]]

    def export_to_otlp(self, transport: Any) -> int:
        """把当前 spans 转成 OTLP 事件投递给外部 transport (尽力而为)。

        transport 需提供 ``async send(events)`` (见 ``ports.telemetry.otel.OtlpTransport``)。
        与 otel payload 构建联动: 每个事件的 context 携带规范化后的 trace_id / span_id /
        parent_span_id, 使 OTLP 后端能还原 span 树与 trace 关联。失败静默降级, 不抛。
        """
        spans = list(self._spans)
        if not spans:
            return 0
        try:
            from ..ports.telemetry.types import EnrichedTelemetryEvent
        except Exception:  # noqa: BLE001
            return 0

        events = []
        for s in spans:
            attrs = dict(s.attributes)
            attrs["status"] = s.status
            attrs["duration_ms"] = round(s.duration_ms, 2)
            ctx = {
                "trace_id": (s.trace_id or "").replace("-", ""),
                "span_id": (_hex(s.span_id)),
            }
            if s.parent_id:
                ctx["parent_span_id"] = _hex(s.parent_id)
            events.append(EnrichedTelemetryEvent(
                event=s.name, timestamp=s.start_time, properties=attrs, context=ctx,
            ))
        try:
            import asyncio
            import threading

            def _runner() -> None:
                asyncio.run(transport.send(events))

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                _runner()  # 没有运行中 loop: 同步跑完
            else:
                # 已有运行中 loop (如 asyncio 主循环): 放后台线程避免阻塞
                threading.Thread(target=_runner, daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            log.debug("OTLP export skipped: %s", exc)
        return len(events)

    def clear(self) -> None:
        """清空所有数据。"""
        with self._lock:
            self._spans.clear()
            self._active_spans.clear()
            self._traces.clear()
            self._metrics = {
                "total_traces": 0,
                "total_spans": 0,
                "total_errors": 0,
                "total_tokens": 0,
                "total_tool_calls": 0,
                "tool_call_latencies": deque(maxlen=100),
                "model_call_latencies": deque(maxlen=100),
            }

    # ---- 自动导出 (长时间运行防内存泄漏) ----

    _AUTO_EXPORT_THRESHOLD = 500  # span 数达到此阈值时自动导出
    _last_export_count: int = 0

    def should_auto_export(self) -> bool:
        """判断是否应自动导出 (span 数超过阈值且未导出过)。"""
        with self._lock:
            return len(self._spans) >= self._AUTO_EXPORT_THRESHOLD

    def auto_export(self) -> int:
        """自动导出并清理已导出的 span, 返回导出数量。

        长时间运行的 Agent 会话中, span 累积可能超过内存上限;
        auto_export() 把旧 span 落盘后清空, 防止内存无限增长。
        """
        exported = 0
        with self._lock:
            if len(self._spans) < self._AUTO_EXPORT_THRESHOLD:
                return 0
            # 导出前半部分 (保留最近的一半)
            half = len(self._spans) // 2
            spans_list = list(self._spans)
            to_export = spans_list[:half]
            self._spans = deque(spans_list[half:], maxlen=self._max_spans)
        # 导出到文件 (锁外)
        try:
            export_path = self._telemetry_dir / f"spans_auto_{int(time.time())}.jsonl"
            with open(export_path, "w", encoding="utf-8") as f:
                for span in to_export:
                    f.write(json.dumps(span.to_dict(), ensure_ascii=False) + "\n")
            exported = len(to_export)
            log.debug("Telemetry auto-exported %d spans to %s", exported, export_path)
        except Exception as exc:
            log.debug("Telemetry auto-export failed: %s", exc)
        return exported
