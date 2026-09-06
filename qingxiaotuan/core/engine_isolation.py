# -*- coding: utf-8 -*-
"""引擎调用隔离 —— 把"进程级隔离"做成真机制(可配置、可验证、安全不退化)。

背景
----
README 曾声称 9 个引擎"以隔离的 JSONL 子进程运行", 但真实运行路径
(tools/ core/) 全是 ``from ..ext.xxx import ...`` 的进程内直调,
只有 ``qxt ext ...`` CLI 走 ExternalEngineManager 的子进程。
本模块把两条路统一收口, 并让"子进程隔离"成为**可开关、可统计、可验证**的真机制。

安全底线(重要)
--------------
**判定类引擎(safety)永远走进程内**, 不受隔离开关影响。原因:
红线判定若走子进程, 一旦 spawn 失败 / IPC 超时 / 崩溃, 兜底逻辑很容易变成
"放行"(fail-open), 等于给致命命令开后门。进程内直调没有这个失效模式。
非判定类引擎(diff/crypto/index/ansi/json/search/notify/rules)是纯函数,
子进程失败后回落到进程内是安全的, 因此允许开启隔离。

用法
----
    from qingxiaotuan.core.engine_isolation import get_isolation

    iso = get_isolation()
    iso.call("diff", "unified_diff", {"a": a, "b": b})
    iso.call("safety", "is_redline", {"cmd": cmd})   # 恒走进程内
    print(iso.stats)   # 实际路由统计, 用于验证隔离是否真的生效
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

#: 判定类引擎 —— 结果直接决定"放行/拦截", 必须进程内执行(fail-closed)
SAFETY_CRITICAL_ENGINES = frozenset({"safety"})

#: 允许走子进程隔离的引擎(纯函数, 失败可安全回落)
ISOLATABLE_ENGINES = frozenset({
    "diff", "crypto", "index", "ansi", "json", "search", "notify", "rules",
})

CONFIG_KEY = "engine.isolation"


def is_safety_critical(engine: str) -> bool:
    """该引擎的判定是否直接决定安全放行?"""
    return engine in SAFETY_CRITICAL_ENGINES


def is_isolatable(engine: str) -> bool:
    return engine in ISOLATABLE_ENGINES


class EngineIsolation:
    """引擎调用路由器: 按策略选择进程内 / 子进程, 并记录真实路由。"""

    def __init__(self, isolation_enabled: bool = False, timeout: float = 30.0):
        self.isolation_enabled = bool(isolation_enabled)
        self.timeout = float(timeout)
        # 路由统计: 用于证明"隔离真的生效了", 而不是嘴上说说
        self.stats: Dict[str, int] = {
            "inprocess_safety": 0,   # 判定类(强制进程内)
            "inprocess_default": 0,  # 未开启隔离
            "subprocess": 0,         # 真正走了子进程
            "fallback_inprocess": 0, # 子进程失败后回落
        }
        self.errors: list[str] = []
        self._clients: Dict[str, Any] = {}
        # 进程内引擎实例缓存: 与原 tools/external.py 的 _ENGINE_INSTANCES 行为一致,
        # 避免每次调用重新实例化(某些引擎初始化较重)。
        self._instances: Dict[str, Any] = {}

    # ------------------------------------------------------------------ 配置
    @classmethod
    def from_config(cls, config: Optional[dict] = None) -> "EngineIsolation":
        """从配置读取 engine.isolation 开关(默认关闭, 保持既有行为)。"""
        cfg = config or {}
        enabled = cfg.get("engine", {}).get("isolation", False)
        timeout = cfg.get("engine", {}).get("isolation_timeout", 30.0)
        return cls(isolation_enabled=enabled, timeout=timeout)

    # ------------------------------------------------------------------ 调用
    def call(
        self,
        engine: str,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """调用引擎方法, 自动选择执行位置。"""
        params = params or {}
        timeout = timeout if timeout is not None else self.timeout

        # 1) 判定类: 永远进程内(安全底线, 不看开关)
        if is_safety_critical(engine):
            self.stats["inprocess_safety"] += 1
            return self._call_inprocess(engine, method, params)

        # 2) 未开启隔离: 维持现状(进程内直调)
        if not self.isolation_enabled or not is_isolatable(engine):
            self.stats["inprocess_default"] += 1
            return self._call_inprocess(engine, method, params)

        # 3) 开启隔离: 走子进程; 失败则安全回落
        try:
            result = self._call_subprocess(engine, method, params, timeout)
            self.stats["subprocess"] += 1
            return result
        except Exception as exc:  # 非判定类引擎, 回落是安全的
            self.stats["fallback_inprocess"] += 1
            msg = "engine %s.%s subprocess failed, fallback in-process: %s" % (
                engine, method, exc,
            )
            self.errors.append(msg)
            log.debug(msg)
            return self._call_inprocess(engine, method, params)

    # ---------------------------------------------------------------- 执行体
    def _call_inprocess(self, engine: str, method: str, params: Dict[str, Any]) -> Any:
        """进程内直调 —— 与 tools/external.py 的历史路径逐字节等价。

        同时兼容两种引擎接口:
        1. methods 字典模式 (safety): instance.methods[method](params)
        2. handle(line) IPC 协议模式 (diff/crypto/index/ansi/json/search/notify/rules):
           通过 JSONL 协议转发, 与子进程路径语义完全一致。

        引擎实例按名缓存, 避免每次调用重新实例化(与原 _ENGINE_INSTANCES 行为一致)。
        """
        from qingxiaotuan.ext.registry import get_engine
        import json as _json

        inst = self._instances.get(engine)
        if inst is None:
            inst = get_engine(engine)
            self._instances[engine] = inst

        # 模式1: methods 字典 (safety 等)
        methods = getattr(inst, "methods", None)
        if isinstance(methods, dict):
            handler = methods.get(method)
            if handler is None:
                return {"error": f"引擎 {engine} 未知方法: {method}"}
            return handler(params)

        # 模式2: handle(line) IPC 协议 (与子进程路径相同的 JSONL 封装)
        handle_fn = getattr(inst, "handle", None)
        if callable(handle_fn):
            req = {"id": 1, "method": method, "params": params}
            resp_str = handle_fn(_json.dumps(req, ensure_ascii=False))
            resp = _json.loads(resp_str)
            if resp.get("ok"):
                return resp.get("result")
            return {"error": resp.get("error", f"引擎 {engine} 方法 {method} 失败")}

        # 兜底: 无 methods 字典也无 handle —— 退回实例方法 kwargs 调用
        fn = getattr(inst, method, None)
        if fn is None:
            return {"error": f"引擎 {engine} 无可用接口 (无 methods 字典或 handle 方法)"}
        return fn(**params)

    def _call_subprocess(
        self,
        engine: str,
        method: str,
        params: Dict[str, Any],
        timeout: float,
    ) -> Any:
        """真正的子进程隔离: 复用 IpcClient 的 JSONL stdio 协议。"""
        from qingxiaotuan.core.ipc_client import IpcClient

        client = self._clients.get(engine)
        if client is None:
            client = IpcClient(engine).start()
            self._clients[engine] = client
        return client.request(method, params, timeout=timeout)

    # ------------------------------------------------------------------ 资源
    def close(self) -> None:
        for name, client in list(self._clients.items()):
            try:
                client.close()
            except Exception:
                pass
            self._clients.pop(name, None)

    def report(self) -> str:
        total = sum(self.stats.values())
        if not total:
            return "no engine calls recorded"
        parts = ["%s=%d" % (k, v) for k, v in self.stats.items() if v]
        return "routes(%d): %s" % (total, ", ".join(parts))


# ---------------------------------------------------------------------- 单例
_isolation: Optional[EngineIsolation] = None


def get_isolation() -> EngineIsolation:
    global _isolation
    if _isolation is None:
        _isolation = EngineIsolation.from_config(_load_config())
    return _isolation


def set_isolation(iso: Optional[EngineIsolation]) -> None:
    """供测试注入 / 运行时切换。"""
    global _isolation
    if _isolation is not None:
        _isolation.close()
    _isolation = iso


def _load_config() -> dict:
    """读取合并后的项目配置; 读不到则用 DEFAULT_CONFIG(隔离默认关闭)。

    读合并配置(用户层 config.yaml 覆盖默认值)使 `qxt config set engine.isolation true`
    对后台 worker 等独立进程也生效; 任何异常都安全回退到 DEFAULT_CONFIG。
    """
    try:
        from qingxiaotuan.config import Config

        cfg = Config()
        if isinstance(cfg.data, dict):
            return cfg.data
    except Exception:
        pass
    try:
        from qingxiaotuan.config.defaults import DEFAULT_CONFIG

        return DEFAULT_CONFIG if isinstance(DEFAULT_CONFIG, dict) else {}
    except Exception:
        return {}
