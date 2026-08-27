"""模型调用重试策略 —— 从 Agent 拆出的独立组件。

策略不变:
- 失败按指数退避 + 抖动重试 (backoff * 2^(n-1));
- 触发限流 (rate_limit) 时优先按响应头 Retry-After 退避;
- 鉴权失败 (auth) 立即抛出, 重试无意义;
- 状态码不在 retry_on 白名单内不重试;
- 单次等待封顶 MAX_BACKOFF_WAIT 秒。

拆为组件的原因: 重试是「策略」而非「循环」的一部分 —— 后台 worker、
devloop、nudge 收尾等任何调用方都应共享同一套退避语义。
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

MAX_BACKOFF_WAIT = 60.0

DEFAULT_RETRY_ON = (408, 429, 500, 502, 503, 504)


# ------------------------------------------------------------ 错误分类 / Retry-After

def classify_error(exc: Exception) -> tuple:
    """错误分类 -> (kind, status)。委托给 OpenAI 兼容适配器, 未知类型视为 other。"""
    try:
        from ..models.openai_compat import OpenAICompatAdapter
    except Exception:  # pragma: no cover
        return "other", None
    if hasattr(exc, "status_code") or "openai" in type(exc).__module__.lower():
        return OpenAICompatAdapter.classify_error(exc)
    return "other", None


def retry_after_seconds(exc: Exception) -> Optional[float]:
    """从异常的响应头里解析 Retry-After (秒); 无或非法返回 None。"""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers:
        ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra:
            try:
                return float(ra)
            except (TypeError, ValueError):
                pass
    return None


# ------------------------------------------------------------ 策略本体

def _default_sleep(seconds: float) -> None:
    # 包一层以便测试可 patch 本模块的 time.sleep
    time.sleep(seconds)


def _default_rng(lo: float, hi: float) -> float:
    return random.uniform(lo, hi)


class RetryPolicy:
    """可配置的重试策略。sleep/rng 可注入, 测试无需真等待。"""

    def __init__(
        self,
        max_retries: int = 3,
        backoff: float = 2.0,
        jitter: float = 0.3,
        retry_on=DEFAULT_RETRY_ON,
        sleep: Optional[Callable[[float], None]] = None,
        rng: Optional[Callable[[float, float], float]] = None,
    ) -> None:
        self.max_retries = int(max_retries)
        self.backoff = float(backoff)
        self.jitter = float(jitter)
        self.retry_on = set(retry_on)
        self._sleep = sleep or _default_sleep
        self._rng = rng or _default_rng

    @classmethod
    def from_config(cls, config) -> "RetryPolicy":
        return cls(
            max_retries=config.get("agent.max_retries", 3),
            backoff=config.get("agent.retry_backoff", 2.0),
            jitter=config.get("agent.retry_jitter", 0.3),
            retry_on=set(config.get("agent.retry_on", list(DEFAULT_RETRY_ON))),
        )

    # ------------------------------------------------------------ 等待计算

    def wait_for(self, attempt: int, kind: str, exc: Exception) -> float:
        """第 attempt 次失败后应等待的秒数 (指数退避, 限流尊重 Retry-After)。"""
        if kind == "rate_limit":
            wait = retry_after_seconds(exc) or (self.backoff * (2 ** (attempt - 1)))
        else:
            wait = self.backoff * (2 ** (attempt - 1))
        if self.jitter:
            wait += self._rng(0, self.jitter * wait)
        return min(wait, MAX_BACKOFF_WAIT)

    # ------------------------------------------------------------ 带重试执行

    def call(
        self,
        fn: Callable,
        *,
        label: str = "模型",
        emit: Optional[Callable[[str, dict], None]] = None,
        on_rate_limit_notice: Optional[Callable[[str], None]] = None,
    ):
        """执行 fn() 并按策略重试。鉴权失败立即抛 RuntimeError。"""
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                kind, status = classify_error(exc)
                if kind == "auth":
                    raise RuntimeError(
                        f"{label}鉴权失败 (HTTP {status}): 请检查 API Key 是否正确、未过期、"
                        f"且对当前模型有访问权限。"
                    ) from exc
                if attempt >= self.max_retries or (status is not None and status not in self.retry_on):
                    break
                wait = self.wait_for(attempt, kind, exc)
                if emit:
                    emit("model.retry", {
                        "attempt": attempt, "wait": round(wait, 1),
                        "kind": kind, "status": status, "error": str(exc)[:160],
                    })
                if on_rate_limit_notice and kind == "rate_limit":
                    on_rate_limit_notice(
                        f"[连接] 触发限流, {wait:.0f}s 后重试 (第 {attempt}/{self.max_retries} 次)")
                log.debug("%s 调用失败 (第 %d/%d 次, kind=%s, status=%s), %.1fs 后重试: %s",
                          label, attempt, self.max_retries, kind, status, wait, str(exc)[:160])
                self._sleep(wait)
        raise RuntimeError(f"{label}调用失败 (已重试 {self.max_retries} 次): {last_err}") from last_err


# ------------------------------------------------------------ 客户端限流

class RateLimiter:
    """发送前主动节流的客户端限流器: 令牌桶 (每分钟请求数) + 并发信号量。

    与 RetryPolicy 互补: 重试是「被 429 打回来再退避」, 限流是「发送前先节流」,
    避免触发服务端限流 (尤其免费层如 OpenCode Zen: 1 req/s, 10/min)。
    - max_requests_per_minute <= 0 表示不限速;
    - max_concurrent <= 0 表示不限并发;
    - sleep/clock 可注入, 测试无需真等待。
    """

    def __init__(
        self,
        max_requests_per_minute: int = 0,
        max_concurrent: int = 0,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.rate = max(0, int(max_requests_per_minute))
        self._sem = threading.Semaphore(max_concurrent) if max_concurrent > 0 else None
        self._sleep = sleep or _default_sleep
        self._clock = clock or time.monotonic
        self._tokens = float(self.rate)
        self._last = self._clock()
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config) -> "RateLimiter":
        """按 model.rate_limit 配置构造; 未启用或全 0 时返回不限流实例。"""
        if not config.get("model.rate_limit.enabled", False):
            return cls(0, 0)
        return cls(
            max_requests_per_minute=config.get("model.rate_limit.max_requests_per_minute", 0),
            max_concurrent=config.get("model.rate_limit.max_concurrent", 0),
        )

    @property
    def active(self) -> bool:
        return self.rate > 0 or self._sem is not None

    def acquire(self) -> None:
        """发送请求前调用: 先取并发信号量, 再按令牌桶节流。"""
        if self._sem:
            self._sem.acquire()
        if self.rate <= 0:
            return
        while True:
            with self._lock:
                now = self._clock()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.rate, self._tokens + elapsed * self.rate / 60.0)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) * 60.0 / self.rate
            self._sleep(wait)

    def release(self) -> None:
        """请求结束后调用: 释放并发信号量。"""
        if self._sem:
            self._sem.release()
