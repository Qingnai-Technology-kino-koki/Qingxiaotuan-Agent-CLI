# -*- coding: utf-8 -*-
"""锁定 tools/external.py 的引擎调用真的经过 EngineIsolation 路由。

如果没有这条测试, 有人可能把 _call 又改回进程内直调,
那样文档声称的"进程级隔离"开关 (engine.isolation) 就形同虚设 —— 永远走不到子进程。
"""
from __future__ import annotations

from qingxiaotuan.core import engine_isolation as ei
from qingxiaotuan.tools import external


class _FakeIso:
    """可控的隔离路由器替身: 记录调用, 不真正跑引擎。"""

    def __init__(self):
        self.calls = []

    def call(self, engine, method, params, timeout=None):
        self.calls.append((engine, method, params, timeout))
        return {"routed": True, "engine": engine, "method": method}

    def close(self):
        # set_isolation(None) 复位前会对旧对象调用 close; 替身只需实现它,
        # 但绝不能清空 calls —— 否则 finally 里的复位会把断言要用的证据抹掉。
        pass


class _BoomIso:
    def call(self, *a, **k):
        raise RuntimeError("router exploded")

    def close(self):
        pass


def test_external_call_routes_through_isolation_router():
    """external._call 必须把调用委托给 get_isolation().call, 而不是自己直调。"""
    fake = _FakeIso()
    ei.set_isolation(fake)
    try:
        result = external._call("diff", "diff", {"old": "a", "new": "b"}, timeout=5)
    finally:
        ei.set_isolation(None)

    assert len(fake.calls) == 1, "external._call 未委托给隔离路由器"
    engine, method, params, timeout = fake.calls[0]
    assert engine == "diff"
    assert method == "diff"
    assert params == {"old": "a", "new": "b"}
    assert timeout == 5
    assert result == {"routed": True, "engine": "diff", "method": "diff"}


def test_external_call_preserves_error_dict_on_router_failure():
    """路由器抛异常时, external._call 必须兜回 {"error": ...} 而非向上抛。"""
    boom = _BoomIso()
    ei.set_isolation(boom)
    try:
        result = external._call("diff", "diff", {})
    finally:
        ei.set_isolation(None)

    assert isinstance(result, dict) and "error" in result, result
    assert "RuntimeError" in result["error"]


def test_default_isolation_keeps_legacy_inprocess_behavior():
    """默认(未开隔离)下, external._call 结果与历史进程内直调一致 —— 行为不退化。"""
    # 确保用默认单例(隔离关闭)
    ei.set_isolation(None)
    result = external._call("diff", "diff", {"old": "a\nb\n", "new": "a\nc\n"})
    assert isinstance(result, dict) and "diff" in result, result
    assert "c" in result["diff"]


def test_safety_engine_always_inprocess_via_router():
    """即便是隔离开启, 判定类(safety)也必须走进程内, 不能进子进程(安全底线)。"""
    iso = ei.EngineIsolation(isolation_enabled=True)
    ei.set_isolation(iso)
    try:
        result = external._call("safety", "score", {"command": "rm -rf /"})
    finally:
        ei.set_isolation(None)
    assert iso.stats["inprocess_safety"] == 1
    assert iso.stats["subprocess"] == 0
    assert isinstance(result, dict) and "risk" in result
