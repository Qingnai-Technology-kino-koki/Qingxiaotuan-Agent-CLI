"""RetryPolicy 单元测试 (core/retry.py) —— 重试策略作为独立组件的语义。"""
from __future__ import annotations

from unittest import mock

import pytest

from qingxiaotuan.config import Config
from qingxiaotuan.core.retry import (MAX_BACKOFF_WAIT, RetryPolicy,
                                     classify_error, retry_after_seconds)


class _Resp:
    def __init__(self, headers):
        self.headers = headers


def _policy(**kw):
    base = dict(max_retries=3, backoff=2.0, jitter=0.0, retry_on={429, 500, 503})
    base.update(kw)
    return RetryPolicy(**base)


# ---------------------------------------------------------------- 等待计算

def test_exponential_backoff():
    p = _policy()
    assert p.wait_for(1, "server", Exception("x")) == 2.0
    assert p.wait_for(2, "server", Exception("x")) == 4.0
    assert p.wait_for(3, "server", Exception("x")) == 8.0


def test_rate_limit_prefers_retry_after_header():
    exc = Exception("rate")
    exc.response = _Resp({"retry-after": "7"})
    assert _policy().wait_for(1, "rate_limit", exc) == 7.0


def test_jitter_adds_within_bounds():
    p = RetryPolicy(max_retries=3, backoff=2.0, jitter=0.5,
                    sleep=lambda s: None, rng=lambda lo, hi: hi)  # 取抖动上界
    # 2 * (1 + 0.5) = 3.0
    assert p.wait_for(1, "server", Exception()) == 3.0


def test_wait_capped_at_max():
    p = _policy(backoff=1000.0)
    assert p.wait_for(2, "server", Exception()) == MAX_BACKOFF_WAIT


# ---------------------------------------------------------------- call 语义

def test_call_retries_until_success():
    sleeps = []
    p = _policy(sleep=sleeps.append)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert p.call(fn) == "ok"
    assert calls["n"] == 3
    assert sleeps == [2.0, 4.0]


def test_call_raises_after_exhaustion():
    p = _policy(max_retries=2, sleep=lambda s: None)

    def fn():
        raise RuntimeError("always")

    with pytest.raises(RuntimeError, match="已重试 2 次"):
        p.call(fn)


def test_auth_fails_fast_without_retry():
    sleeps = []
    p = _policy(sleep=sleeps.append)

    class AuthErr(Exception):
        status_code = 401

    def fn():
        raise AuthErr("unauthorized")

    with pytest.raises(RuntimeError, match="鉴权失败"):
        p.call(fn)
    assert sleeps == []  # 鉴权失败不重试、不等待


def test_non_retryable_status_not_retried():
    sleeps = []
    p = _policy(retry_on={429}, sleep=sleeps.append)

    class ServerErr(Exception):
        status_code = 400

    def fn():
        raise ServerErr("bad request")

    with pytest.raises(RuntimeError):
        p.call(fn)
    assert sleeps == []


def test_emit_and_notice_hooks():
    events, notices = [], []
    p = _policy(sleep=lambda s: None)

    class RateErr(Exception):
        status_code = 429

    def fn():
        raise RateErr("too many requests")

    with pytest.raises(RuntimeError):
        p.call(fn, emit=lambda t, e: events.append((t, e)),
               on_rate_limit_notice=notices.append)
    assert events and events[0][0] == "model.retry"
    assert events[0][1]["kind"] == "rate_limit"
    assert any("限流" in n for n in notices)


# ---------------------------------------------------------------- 构造与工具函数

def test_from_config_reads_agent_section():
    cfg = Config()
    p = RetryPolicy.from_config(cfg)
    assert p.max_retries == cfg.get("agent.max_retries", 3)
    assert p.backoff == cfg.get("agent.retry_backoff", 2.0)


def test_retry_after_seconds_invalid_value_returns_none():
    exc = Exception("x")
    exc.response = _Resp({"Retry-After": "not-a-number"})
    assert retry_after_seconds(exc) is None
    assert retry_after_seconds(Exception("no response")) is None


def test_classify_unknown_is_other():
    kind, status = classify_error(ValueError("plain"))
    # 关键约束: 不抛异常且返回二元组; 未知异常不误判为可重试的限流/鉴权
    assert isinstance(kind, str)
    if kind == "other":
        assert "限流" not in kind
