"""RateLimiter 单元测试 (core/retry.py) —— 发送前主动节流的令牌桶 + 并发信号量。

与 RetryPolicy 的测试互补: 重试是"被 429 打回来再退避", 限流是"发送前先节流"。
sleep/clock 全部注入, 测试无需真等待。
"""
from __future__ import annotations

import threading

from qingxiaotuan.config import Config
from qingxiaotuan.core.retry import RateLimiter, RetryPolicy


class _Clock:
    """可手动推进的假时钟 (替代 time.monotonic)。"""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_sleep(clock: _Clock):
    """记录每次 sleep 时长, 并同步推进时钟 (模拟真实睡眠流逝)。"""
    sleeps: list = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        clock.advance(s)

    return sleep, sleeps


# ---------------------------------------------------------------- 构造与配置

def test_disabled_by_default():
    rl = RateLimiter.from_config(Config())
    assert rl.rate == 0
    assert rl._sem is None
    assert not rl.active


def test_from_config_enabled():
    cfg = Config()
    cfg.data["model"]["rate_limit"]["enabled"] = True
    cfg.data["model"]["rate_limit"]["max_requests_per_minute"] = 10
    cfg.data["model"]["rate_limit"]["max_concurrent"] = 2
    rl = RateLimiter.from_config(cfg)
    assert rl.rate == 10
    assert rl._sem is not None
    assert rl.active


def test_negative_values_treated_as_disabled():
    rl = RateLimiter(max_requests_per_minute=-5, max_concurrent=-1)
    assert rl.rate == 0
    assert rl._sem is None
    assert not rl.active


def test_active_reflects_rate_and_sem():
    assert not RateLimiter(0, 0).active
    assert RateLimiter(5, 0).active
    assert RateLimiter(0, 3).active


# ---------------------------------------------------------------- 令牌桶

def test_no_throttle_when_disabled():
    sleeps: list = []
    rl = RateLimiter(max_requests_per_minute=0, max_concurrent=0, sleep=sleeps.append)
    rl.acquire()
    rl.acquire()
    assert sleeps == []


def test_token_bucket_allows_burst_then_throttles():
    clock = _Clock()
    sleep, sleeps = _make_sleep(clock)
    rl = RateLimiter(max_requests_per_minute=2, sleep=sleep, clock=clock)
    rl.acquire()
    rl.acquire()  # 初始 2 个令牌: 前两次立即通过
    assert sleeps == []
    rl.acquire()  # 令牌耗尽: 需等 1 个令牌 = 60/2 = 30s
    assert sleeps == [30.0]


def test_token_bucket_refills_over_time():
    clock = _Clock()
    sleep, sleeps = _make_sleep(clock)
    rl = RateLimiter(max_requests_per_minute=2, sleep=sleep, clock=clock)
    rl.acquire()
    rl.acquire()  # 耗尽 2 个令牌
    clock.advance(15.0)  # 15s 回填 0.5 个令牌
    rl.acquire()  # 还需 0.5 个令牌 = 15s (而非满额 30s)
    assert sleeps == [15.0]


def test_tokens_capped_at_rate():
    clock = _Clock()
    sleep, sleeps = _make_sleep(clock)
    rl = RateLimiter(max_requests_per_minute=2, sleep=sleep, clock=clock)
    rl.acquire()  # 消耗 1 个令牌 (剩 1)
    clock.advance(3600.0)  # 闲置 1 小时: 令牌应封顶回 2, 而非无限累积
    rl.acquire()
    rl.acquire()  # 封顶后恰好 2 个: 连续两次通过
    assert sleeps == []
    rl.acquire()  # 再取需等 30s
    assert sleeps == [30.0]


# ---------------------------------------------------------------- 并发信号量

def test_concurrency_blocks_until_release():
    rl = RateLimiter(max_requests_per_minute=0, max_concurrent=1)
    rl.acquire()  # 占满唯一并发槽
    entered = threading.Event()
    acquired = threading.Event()

    def worker():
        entered.set()
        rl.acquire()
        acquired.set()

    t = threading.Thread(target=worker)
    t.start()
    assert entered.wait(timeout=1.0)
    assert not acquired.is_set()  # 并发槽被占, 阻塞中
    rl.release()  # 释放并发槽
    assert acquired.wait(timeout=1.0)  # worker 拿到槽
    rl.release()  # 归还 worker 占用的槽
    t.join(timeout=1.0)


def test_release_is_idempotent_without_sem():
    rl = RateLimiter(max_requests_per_minute=5, max_concurrent=0)
    rl.acquire()
    rl.release()  # 无信号量时 release 应为空操作, 不抛异常


# ---------------------------------------------------------------- 与 RetryPolicy 协同

def test_acquire_release_around_retry_call():
    """模拟 agent 的调用模式: acquire -> retry_policy.call -> release。"""
    clock = _Clock()
    sleep, sleeps = _make_sleep(clock)
    rl = RateLimiter(max_requests_per_minute=0, max_concurrent=1, sleep=sleep, clock=clock)
    policy = RetryPolicy(max_retries=2, backoff=1.0, jitter=0.0, sleep=lambda s: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return "ok"

    rl.acquire()
    try:
        result = policy.call(fn)
    finally:
        rl.release()
    assert result == "ok"
    assert calls["n"] == 2
    # 释放后并发槽可再次获取, 无死锁
    rl.acquire()
    rl.release()
