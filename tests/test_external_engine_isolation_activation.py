# -*- coding: utf-8 -*-
"""引擎进程级隔离的"用户可达激活"链路测试。

证明隔离不再只是休眠模块:
- CLI `--isolation` 能解析并被 main() 应用到 EngineIsolation 单例;
- `qxt config set engine.isolation true` 持久化后对 (含后台 worker 的) 独立进程生效;
- 工具层 external._call 真走隔离路由器 (开启后子进程失败安全回落进程内, 仍得正确结果)。
"""
from __future__ import annotations

import sys

import pytest

from qingxiaotuan.cli import parser
from qingxiaotuan.core import engine_isolation as ei
from qingxiaotuan.core.engine_isolation import (
    EngineIsolation,
    get_isolation,
    set_isolation,
)
from qingxiaotuan.tools import external


# ---------------------------------------------------------- flag 解析
def test_parse_isolation_flag():
    assert parser.build_parser().parse_args(["--isolation"]).isolation is True
    assert parser.build_parser().parse_args([]).isolation is False


# ---------------------------------------------------------- main() 应用单例
def test_main_isolation_flag_enables_singleton(monkeypatch):
    from qingxiaotuan.cli import fast_start

    captured: dict = {}

    def spy(args):
        captured["enabled"] = get_isolation().isolation_enabled
        return 0

    monkeypatch.setattr(fast_start, "fast_chat", spy)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["qxt", "--isolation"])
    set_isolation(None)
    try:
        parser.main()
        assert captured.get("enabled") is True
    finally:
        set_isolation(None)


# ---------------------------------------------------------- 配置持久化生效
def test_config_set_engine_isolation_honored():
    from qingxiaotuan.config import Config

    try:
        Config().set_user("engine.isolation", True)
        set_isolation(None)  # 强制从合并配置重建单例
        assert get_isolation().isolation_enabled is True
    finally:
        try:
            Config().set_user("engine.isolation", False)
        except Exception:
            pass
        set_isolation(None)


# ---------------------------------------------------------- 工具层真走路由器
def test_external_call_routes_through_isolation_fallback(monkeypatch):
    """开启隔离后, external._call 经 EngineIsolation 路由; 子进程失败安全回落进程内。"""
    iso = EngineIsolation(isolation_enabled=True)
    set_isolation(iso)
    try:

        class _Boom:
            def __init__(self, *a, **k):
                pass

            def start(self):
                return self

            def request(self, *a, **k):
                raise RuntimeError("subprocess died")

            def close(self):
                pass

        monkeypatch.setattr("qingxiaotuan.core.ipc_client.IpcClient", _Boom)
        res = external._call("diff", "diff", {"old": "a\n", "new": "b\n"})
        assert "b" in res["diff"]  # 回落进程内仍得出正确结果
        assert iso.stats["fallback_inprocess"] == 1
        assert iso.stats["subprocess"] == 0
    finally:
        set_isolation(None)
