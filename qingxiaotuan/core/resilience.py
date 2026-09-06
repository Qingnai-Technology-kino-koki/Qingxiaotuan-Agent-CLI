"""Agent 韧性组件 —— 从 Agent 拆出的独立模块。

职责:
- 带超时/重试/熔断的模型调用 (_chat_with_retry)
- 韧性状态查询 (resilience_status), 供 /status 展示
- 重试策略属性的兼容代理 (max_retries / retry_backoff 等)

三层防线:
  RateLimiter (令牌桶限流) → RetryPolicy (指数退避重试) → CircuitBreaker (熔断快失败)

拆出原因:
- Agent 中 _chat_with_retry + resilience_status + retry 属性代理合计 ~130 行,
  与 ReAct 主循环的职责边界模糊; 拆出后 Agent 只需组合 self._resilience = AgentResilience(...)
- DevLoop、Swarm Worker 等也需要韧性能力, 共享同一实现避免重复
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from .retry import RetryPolicy, RateLimiter, CircuitBreaker

log = logging.getLogger(__name__)


class AgentResilience:
    """韧性组件: 三层防线封装 + 兼容代理。

    用法::

        res = AgentResilience(config, kernel)
        result = res.chat_with_retry(model, messages, tools, stream, on_token, on_reason)
        status = res.status()
    """

    def __init__(self, config: Any, kernel: Any) -> None:
        self.config = config
        self.kernel = kernel
        self.retry_policy = RetryPolicy.from_config(config)
        self._rate_limiter = RateLimiter.from_config(config)
        self._circuit_breaker = CircuitBreaker.from_config(config)

    def chat_with_retry(
        self,
        model: Any,
        messages: list,
        tools: list,
        stream: bool,
        on_token: Optional[Callable] = None,
        on_reason: Optional[Callable] = None,
        label: str = "模型",
    ) -> Any:
        """带超时/重试/熔断的模型调用: 三层韧性 = RateLimiter(节流) + RetryPolicy(退避重试) + CircuitBreaker(熔断快失败)。"""
        def _attempt():
            self._rate_limiter.acquire()
            try:
                return self.retry_policy.call(
                    lambda: model.chat(
                        messages, tools=tools, stream=stream,
                        on_token=on_token, on_reason=on_reason,
                    ),
                    label=label,
                    emit=self.kernel.emit,
                    on_rate_limit_notice=on_reason,
                )
            finally:
                self._rate_limiter.release()

        # 测试可能用 Agent.__new__ 跳过 __init__ (未设置 _circuit_breaker), 此时退化为直连
        breaker = getattr(self, "_circuit_breaker", None)
        if breaker is None:
            return _attempt()
        return breaker.call(_attempt, label=label)

    def status(self) -> Dict[str, Any]:
        """返回韧性组件 (熔断/限流/重试) 的运行状态, 供 /status 展示。"""
        breaker = self._circuit_breaker
        if breaker is None:
            breaker_state: Dict[str, Any] = {"enabled": False, "state": "closed"}
        else:
            breaker_state = breaker.stats()
            breaker_state.setdefault("failure_threshold", getattr(breaker, "failure_threshold", 0))
            breaker_state.setdefault("cooldown", getattr(breaker, "cooldown", 0.0))

        limiter = self._rate_limiter
        if limiter is None:
            limiter_info: Dict[str, Any] = {"active": False, "max_rpm": 0, "max_concurrent": 0}
        else:
            limiter_info = {
                "active": bool(getattr(limiter, "active", False)),
                "max_rpm": getattr(limiter, "rate", 0),
                "max_concurrent": getattr(limiter, "_max_concurrent", 0),
            }

        rp = self.retry_policy
        if rp is None:
            retry_info: Dict[str, Any] = {"max_retries": 0, "backoff": 0.0, "retry_on": []}
        else:
            retry_info = {
                "max_retries": getattr(rp, "max_retries", 0),
                "backoff": getattr(rp, "backoff", 0.0),
                "retry_on": sorted(getattr(rp, "retry_on", set())),
            }

        return {
            "circuit_breaker": breaker_state,
            "rate_limiter": limiter_info,
            "retry": retry_info,
        }

    # ---- 兼容代理 (外部测试/调用方仍可读写 agent.max_retries 等) ----

    @property
    def max_retries(self) -> int:
        return self.retry_policy.max_retries

    @max_retries.setter
    def max_retries(self, v) -> None:
        self.retry_policy.max_retries = int(v)

    @property
    def retry_backoff(self) -> float:
        return self.retry_policy.backoff

    @retry_backoff.setter
    def retry_backoff(self, v) -> None:
        self.retry_policy.backoff = float(v)

    @property
    def retry_jitter(self) -> float:
        return self.retry_policy.jitter

    @retry_jitter.setter
    def retry_jitter(self, v) -> None:
        self.retry_policy.jitter = float(v)

    @property
    def retry_on(self) -> set:
        return self.retry_policy.retry_on

    @retry_on.setter
    def retry_on(self, v) -> None:
        self.retry_policy.retry_on = set(v)
