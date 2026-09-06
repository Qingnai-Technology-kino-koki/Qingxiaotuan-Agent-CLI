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
                # 可重试判定:
                #  - 无 HTTP 状态码 (网络/超时类瞬时错误) → 一律可重试;
                #  - 有状态码 → 仅在 retry_on 白名单内才重试;
                #  - 超出重试预算 → 不再重试。
                if status is None:  # 网络/超时/连接等瞬时错误
                    retriable = True
                else:
                    retriable = status in self.retry_on
                if not retriable or attempt >= self.max_retries:
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
        self._max_concurrent = max(0, int(max_concurrent))
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


# ------------------------------------------------------------ 熔断器 (Circuit Breaker)

class CircuitOpen(RuntimeError):
    """熔断器开启时, 调用被快速失败而非打到已故障的上游。"""


class CircuitBreaker:
    """调用级熔断器: 与 RetryPolicy / RateLimiter 互补的第三道韧性防线。

    - RetryPolicy  : 被 429/5xx 打回来后「退避重试」(每次仍真实打到上游);
    - RateLimiter  : 发送前「主动节流」避免触发限流;
    - CircuitBreaker: 连续失败达阈值后「直接熔断」, 在冷却期内对所有调用快速失败,
                      不再浪费重试预算去打一个已经挂掉的上游 (省 token / 省时间 / 快失败)。

    状态机:
        CLOSED  --(连续失败 >= failure_threshold)-->  OPEN
        OPEN    --(冷却 cooldown 到期, 放行一次试探)-->  HALF_OPEN
        HALF_OPEN --(连续成功 >= success_threshold)--> CLOSED
        HALF_OPEN --(试探失败)-->  OPEN (重置冷却计时)

    线程安全 (单实例可被多个 worker 共享)。clock 可注入, 测试无需真等待。
    enabled=False 时退化为透传 (call 直接执行 fn), 与未接入完全一致。
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown: float = 30.0,
        success_threshold: int = 1,
        clock: Optional[Callable[[], float]] = None,
        enabled: bool = True,
    ) -> None:
        self.enabled = bool(enabled)
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown = float(cooldown)
        self.success_threshold = max(1, int(success_threshold))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = 0.0

    @classmethod
    def from_config(cls, config) -> "CircuitBreaker":
        """按 agent.circuit_breaker.* 配置构造; 未启用时返回透传实例 (不触发任何熔断)。"""
        if not config.get("agent.circuit_breaker.enabled", False):
            return cls(enabled=False)
        return cls(
            failure_threshold=config.get("agent.circuit_breaker.failure_threshold", 5),
            cooldown=config.get("agent.circuit_breaker.cooldown", 30.0),
            success_threshold=config.get("agent.circuit_breaker.success_threshold", 1),
        )

    # ------------------------------------------------------------ 查询 / 控制

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def stats(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "failures": self._failures,
                "successes": self._successes,
                "opened_at": self._opened_at,
                "enabled": self.enabled,
            }

    def reset(self) -> None:
        """复位到 CLOSED (运维手动恢复 / 测试用)。"""
        with self._lock:
            self._state = self.CLOSED
            self._failures = 0
            self._successes = 0
            self._opened_at = 0.0

    def trip(self) -> None:
        """手动熔断 (立即进入 OPEN, 重置冷却计时)。"""
        with self._lock:
            self._state = self.OPEN
            self._opened_at = self._clock()

    # ------------------------------------------------------------ 带熔断执行

    def call(self, fn: Callable, *, label: str = "调用"):
        """执行 fn(); 熔断器开启且冷却未到则快速失败 (CircuitOpen)。"""
        if not self.enabled:
            return fn()
        with self._lock:
            if self._state == self.OPEN:
                remaining = self.cooldown - (self._clock() - self._opened_at)
                if remaining <= 0:
                    self._state = self.HALF_OPEN
                    self._successes = 0
                else:
                    raise CircuitOpen(
                        f"{label}熔断器开启中 (约 {remaining:.0f}s 后重试), 已快速失败以避免反复打到故障上游"
                    )
            half_open = self._state == self.HALF_OPEN
        try:
            result = fn()
        except Exception:  # noqa: BLE001
            with self._lock:
                self._failures += 1
                if half_open:
                    self._state = self.OPEN
                    self._opened_at = self._clock()
                elif self._failures >= self.failure_threshold:
                    self._state = self.OPEN
                    self._opened_at = self._clock()
            raise
        with self._lock:
            if half_open:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    self._state = self.CLOSED
                    self._failures = 0
            else:
                self._failures = 0
        return result
