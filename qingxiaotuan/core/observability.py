"""Agent 可观测性组件 —— 从 Agent 拆出的独立模块。

职责:
- 可观测性初始化 (TelemetryCollector, 可选)
- 用量累计 (_accumulate_usage)
- 成本估算 (_estimate_total_cost)
- 缓存命中率 (cache_hit_rate)
- 可观测性 span 创建/结束 (start_span / finish_span)
- 遥测状态查询 (telemetry_status), 供 /stats 展示

拆出原因:
- Agent 中 telemetry 相关逻辑分散在 __init__、_chat_with_retry、_accumulate_usage
  等多处, 合计 ~150 行; 拆出后 Agent 只需调用 self._obs.accumulate_usage(usage)
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)


class AgentObservability:
    """可观测性组件: 遥测、用量、成本的统一封装。

    用法::

        obs = AgentObservability(config, home)
        obs.accumulate_usage(usage)
        cost = obs.estimate_total_cost()
        rate = obs.cache_hit_rate()
        span = obs.start_span("model.chat", trace_id)
        obs.finish_span(span.span_id, status="ok")
    """

    def __init__(self, config: Any, home: Any = None) -> None:
        self.config = config
        self._telemetry = None
        self._trace_id = ""
        self.total_usage: Dict[str, int] = {}

        if config.get("observability.telemetry.enabled", False):
            try:
                from pathlib import Path
                from .telemetry import TelemetryCollector
                tc = TelemetryCollector(Path(str(home or config.home)))
                self._telemetry = tc
                self._trace_id = tc.start_trace("agent.session")
            except Exception as exc:  # noqa: BLE001
                log.debug("Telemetry 初始化失败, 已禁用可观测性: %s", exc)

    @property
    def telemetry(self):
        return self._telemetry

    @property
    def trace_id(self):
        return self._trace_id

    @trace_id.setter
    def trace_id(self, value: str):
        self._trace_id = value

    # ---- 用量累计 ----

    def accumulate_usage(self, usage: Any, session_append: Optional[Callable] = None,
                         config: Any = None, turn_count: int = 0) -> None:
        """累计模型用量 (含 DeepSeek 的 prompt cache 命中字段), 并把本次增量落盘会话流。"""
        if not usage:
            return
        cfg = config or self.config
        delta: Dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens",
                     "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            val = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            if isinstance(val, (int, float)):
                delta[key] = int(val)
                self.total_usage[key] = self.total_usage.get(key, 0) + int(val)
        if not delta:
            return
        if session_append is not None:
            provider = cfg.get("model.provider", "")
            model = cfg.get("model.model", "")
            cost_usd: Optional[float]
            try:
                from ..models.router import estimate_cost
                cost_usd = estimate_cost(provider, model,
                                         delta.get("prompt_tokens", 0),
                                         delta.get("completion_tokens", 0))
            except Exception:  # noqa: BLE001
                cost_usd = None
            session_append(
                "usage",
                turn=turn_count,
                delta=delta,
                provider=provider,
                model=model,
                cost_usd=cost_usd,
            )

    # ---- 成本估算 ----

    def estimate_total_cost(self) -> float:
        """估算当前会话总花费 (USD)。"""
        from ..core.agent_helpers import estimate_total_cost as _etc
        return _etc(self.config, self.total_usage)

    # ---- 缓存命中率 ----

    def cache_hit_rate(self) -> Optional[float]:
        """DeepSeek 前缀缓存命中率: hit / (hit + miss)。无数据返回 None。"""
        from ..core.agent_helpers import cache_hit_rate as _chr
        return _chr(self.total_usage)

    # ---- Span 管理 ----

    def start_span(self, name: str, trace_id: str = "", attributes: Optional[Dict] = None):
        """创建可观测性 span。默认关闭 → 返回 None。"""
        if self._telemetry is not None:
            return self._telemetry.start_span(name, trace_id=trace_id or self._trace_id, attributes=attributes)
        return None

    def finish_span(self, span_id: str, status: str = "ok") -> None:
        """结束可观测性 span。"""
        if self._telemetry is not None:
            self._telemetry.finish_span(span_id, status=status)

    def add_span_event(self, span, event_name: str, attributes: Optional[Dict] = None) -> None:
        """给 span 添加事件。"""
        if span is not None and self._telemetry is not None:
            span.add_event(event_name, attributes or {})

    def record_model_call(self, elapsed_ms: float, tokens: int, success: bool = True) -> None:
        """记录模型调用指标。"""
        if self._telemetry is not None:
            self._telemetry.record_model_call(elapsed_ms, tokens, success=success)

    def record_tool_call(self, name: str, elapsed_ms: float, success: bool = True) -> None:
        """记录工具调用指标。"""
        if self._telemetry is not None:
            self._telemetry.record_tool_call(name, elapsed_ms, success=success)

    # ---- Trace 管理 ----

    def start_trace(self, name: str) -> str:
        """开始新的 trace。"""
        if self._telemetry is not None:
            tid = self._telemetry.start_trace(name)
            self._trace_id = tid
            return tid
        return ""

    def finish_trace(self, trace_id: str) -> None:
        """结束 trace。"""
        if self._telemetry is not None:
            self._telemetry.finish_trace(trace_id)

    # ---- 状态查询 ----

    def status(self) -> Dict[str, Any]:
        """返回可观测性状态, 供 /stats 展示。"""
        enabled = self._telemetry is not None
        return {
            "enabled": enabled,
            "trace_id": self._trace_id,
            "total_usage": dict(self.total_usage),
        }
