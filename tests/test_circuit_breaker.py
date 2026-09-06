"""CircuitBreaker 单元测试 —— 验证状态机与快速失败语义。

覆盖:
- 关闭态透传成功 / 失败计数
- 连续失败达阈值 -> OPEN
- OPEN 冷却期内快速失败 (CircuitOpen) 且不真正调用 fn
- 冷却到期 -> HALF_OPEN 试探; 成功 -> CLOSED
- HALF_OPEN 试探失败 -> 重新 OPEN
- success_threshold > 1 需多次成功才恢复
- enabled=False 退化为纯透传
- from_config 按开关构造
"""
import time

from qingxiaotuan.core.retry import CircuitBreaker, CircuitOpen


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _breaker(clock=None, **kw):
    return CircuitBreaker(clock=clock or FakeClock(), **kw)


def test_closed_passes_through_and_resets_failures():
    cb = _breaker()
    assert cb.state == "closed"
    assert cb.call(lambda: 42) == 42
    assert cb.stats()["failures"] == 0
    # 一次失败不会熔断
    try:
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    except RuntimeError:
        pass
    assert cb.state == "closed"
    assert cb.stats()["failures"] == 1
    # 成功后计数清零
    cb.call(lambda: 1)
    assert cb.stats()["failures"] == 0


def test_opens_after_failure_threshold():
    cb = _breaker(failure_threshold=3)
    for _ in range(3):
        try:
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
        except RuntimeError:
            pass
    assert cb.state == "open"


def test_open_fast_fails_without_calling_fn():
    cb = _breaker(failure_threshold=2, cooldown=30.0)
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise RuntimeError("x")

    for _ in range(2):
        try:
            cb.call(bad)
        except RuntimeError:
            pass
    assert cb.state == "open"
    # 冷却期内: 快速失败, fn 不再执行
    try:
        cb.call(bad)
    except CircuitOpen:
        pass
    else:
        raise AssertionError("expected CircuitOpen")
    assert calls["n"] == 2  # fn 未被第三次调用


def test_half_open_recovers_after_cooldown():
    clock = FakeClock(1000.0)
    cb = _breaker(failure_threshold=2, cooldown=10.0, clock=clock)
    for _ in range(2):
        try:
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
        except RuntimeError:
            pass
    assert cb.state == "open"
    # 冷却未到
    try:
        cb.call(lambda: 1)
    except CircuitOpen:
        pass
    else:
        raise AssertionError("expected CircuitOpen before cooldown")
    # 冷却到期 -> 半开试探成功 -> 关闭
    clock.advance(11.0)
    assert cb.call(lambda: "ok") == "ok"
    assert cb.state == "closed"
    assert cb.stats()["failures"] == 0


def test_half_open_failure_reopens():
    clock = FakeClock(1000.0)
    cb = _breaker(failure_threshold=1, cooldown=10.0, clock=clock)
    try:
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    except RuntimeError:
        pass
    assert cb.state == "open"
    clock.advance(11.0)
    # 半开试探失败 -> 重新 open
    try:
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    except RuntimeError:
        pass
    assert cb.state == "open"
    # 冷却重置: 需再次等待
    try:
        cb.call(lambda: 1)
    except CircuitOpen:
        pass
    else:
        raise AssertionError("expected CircuitOpen (cooldown reset)")


def test_success_threshold_multiple():
    clock = FakeClock(1000.0)
    cb = _breaker(failure_threshold=1, cooldown=10.0, success_threshold=3, clock=clock)
    try:
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    except RuntimeError:
        pass
    assert cb.state == "open"
    clock.advance(11.0)
    # 半开期需连续成功 3 次
    assert cb.call(lambda: 1) == 1
    assert cb.state == "half_open"
    assert cb.call(lambda: 1) == 1
    assert cb.state == "half_open"
    assert cb.call(lambda: 1) == 1
    assert cb.state == "closed"


def test_disabled_is_passthrough():
    cb = _breaker(enabled=False)
    assert cb.call(lambda: 7) == 7
    # 即使连续失败, 也不熔断
    for _ in range(10):
        try:
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
        except RuntimeError:
            pass
    assert cb.state == "closed"
    assert cb.call(lambda: 7) == 7


def test_trip_and_reset():
    cb = _breaker()
    cb.trip()
    assert cb.state == "open"
    try:
        cb.call(lambda: 1)
    except CircuitOpen:
        pass
    else:
        raise AssertionError("expected CircuitOpen after manual trip")
    cb.reset()
    assert cb.state == "closed"
    assert cb.call(lambda: 1) == 1


def test_from_config_gating():
    class Cfg:
        def get(self, key, default=None):
            return {"agent.circuit_breaker.enabled": True,
                    "agent.circuit_breaker.failure_threshold": 4,
                    "agent.circuit_breaker.cooldown": 12.0,
                    "agent.circuit_breaker.success_threshold": 2}.get(key, default)

    cb = CircuitBreaker.from_config(Cfg())
    assert cb.enabled and cb.failure_threshold == 4 and cb.cooldown == 12.0

    class CfgOff:
        def get(self, key, default=None):
            return default

    cb2 = CircuitBreaker.from_config(CfgOff())
    assert not cb2.enabled
