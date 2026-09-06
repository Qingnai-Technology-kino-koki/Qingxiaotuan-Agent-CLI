# -*- coding: utf-8 -*-
"""引擎进程级隔离机制的真验证。

不依赖任何"看起来像"的开关: 直接断言路由统计与真实子进程 pid,
证明「隔离」要么真的 spawn 了子进程, 要么安全地回落进程内 —— 而不是摆设。
"""
from __future__ import annotations

import os

import pytest

from qingxiaotuan.core import engine_isolation as ei


# ---------------------------------------------------------- 配置: 默认关闭
def test_from_config_default_isolation_off():
    iso = ei.EngineIsolation.from_config({})
    assert iso.isolation_enabled is False


def test_from_config_enables_isolation():
    iso = ei.EngineIsolation.from_config({"engine": {"isolation": True}})
    assert iso.isolation_enabled is True


def test_module_constants_classify_engines():
    assert ei.is_safety_critical("safety") is True
    assert ei.is_isolatable("diff") is True
    # 未知引擎: 既非安全判定类, 也不可隔离(将走进程内默认路径)
    assert ei.is_safety_critical("nope") is False
    assert ei.is_isolatable("nope") is False


# ---------------------------------------------------------- 安全底线: 永远进程内
def test_safety_always_runs_inprocess_even_when_isolation_on():
    iso = ei.EngineIsolation(isolation_enabled=True)
    result = iso.call("safety", "score", {"command": "rm -rf /"})
    # 判定类引擎: 必须进程内, 不看开关, 不进子进程
    assert iso.stats["inprocess_safety"] == 1
    assert iso.stats["subprocess"] == 0
    assert iso.stats["fallback_inprocess"] == 0
    assert isinstance(result, dict) and "risk" in result


# ---------------------------------------------------------- 默认: 进程内直调
def test_isolation_off_keeps_legacy_inprocess_behavior():
    iso = ei.EngineIsolation(isolation_enabled=False)
    result = iso.call("diff", "diff", {"old": "a\nb\n", "new": "a\nc\n"})
    assert iso.stats["inprocess_default"] == 1
    assert iso.stats["subprocess"] == 0
    assert "c" in result["diff"]


# ---------------------------------------------------------- 开启: 真子进程
@pytest.mark.slow
def test_isolation_on_uses_real_subprocess():
    iso = ei.EngineIsolation(isolation_enabled=True, timeout=30.0)
    try:
        result = iso.call("diff", "diff", {"old": "a\nb\n", "new": "a\nc\n"})
        # 真的走了子进程路由
        assert iso.stats["subprocess"] == 1
        assert iso.stats["inprocess_default"] == 0
        # 真的是另一个进程(pid 不同)
        client = iso._clients.get("diff")
        assert client is not None, "应已建立 IpcClient"
        assert client.proc.pid != os.getpid()
        assert "c" in result["diff"]
    finally:
        iso.close()


# ---------------------------------------------------------- 子进程失败: 安全回落
def test_subprocess_failure_falls_back_to_inprocess(monkeypatch):
    iso = ei.EngineIsolation(isolation_enabled=True)

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def start(self):
            return self

        def request(self, *a, **k):
            raise RuntimeError("subprocess died")

        def close(self):
            pass

    # 让 _call_subprocess 里 import 的 IpcClient 变成必然失败的桩
    monkeypatch.setattr("qingxiaotuan.core.ipc_client.IpcClient", _Boom)

    result = iso.call("diff", "diff", {"old": "a\n", "new": "b\n"})
    assert iso.stats["subprocess"] == 0
    assert iso.stats["fallback_inprocess"] == 1
    assert "b" in result["diff"]  # 回落进程内后仍得出正确结果


# ---------------------------------------------------------- 统计 / 报告
def test_report_reflects_routing():
    iso = ei.EngineIsolation(isolation_enabled=False)
    iso.call("diff", "diff", {"old": "x\n", "new": "y\n"})
    assert "inprocess_default=1" in iso.report()
