"""复盘器 (Reflector) —— 从内核事件流抽取可改进经验。

每个工具执行都会 emit `tool.executed` 事件 (见 tools/base.py)。
复盘器读取内核事件流 (append-only, kernel.events), 产出结构化 Experience:
  - failure:    执行报错 (按 工具+错误类型 聚类, 汇总频次)
  - denied:     用户/策略拒绝的危险操作 (高频项需沉淀为规则)
  - slow:       慢操作 (elapsed 超阈值, 建议缓存/拆分)
  - repeated_ok: 某工具高频成功 (可提炼为技能)

这些 Experience 是「自我改进」的原料 (rulegen / skillgen / distiller 消费)。
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Experience:
    kind: str                      # failure | denied | slow | repeated_ok
    tool: str
    summary: str
    count: int = 1
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {"kind": self.kind, "tool": self.tool, "summary": self.summary, "count": self.count}
        if self.detail:
            d["detail"] = self.detail
        return d


class Reflector:
    """从内核事件流 (kernel.events) 抽取经验。

    增强点 (对标 CC 的可观测闭环):
    - 失败按 (工具, 错误类型) 聚类, 避免同源错误碎片化
    - 支持时间窗: 只统计最近 window_s 内的事件 (默认全量, 保持向后兼容)
    - 高频成功阈值可配置 (默认 5, 与旧版一致)
    """

    SLOW_THRESHOLD = 5.0          # 秒, 超过视为慢操作
    MIN_FREQ = 2                  # 拒绝/慢操作至少出现这么多次才值得沉淀
    OK_SKILL_THRESHOLD = 5        # 高频成功达到该次数才提炼为技能经验

    def __init__(self, kernel: Any = None, window_s: Optional[float] = None) -> None:
        self._kernel = kernel
        self._window_s = window_s

    def reflect(self, events: Optional[List[Any]] = None) -> List[Experience]:
        """从给定事件列表 (缺省取内核事件流) 抽取经验。"""
        if events is None:
            events = list(self._kernel.events) if self._kernel is not None else []
        execs: List[Dict[str, Any]] = []
        for ev in events:
            payload = ev.payload if hasattr(ev, "payload") else ev
            if isinstance(payload, dict) and payload.get("type") == "tool.executed":
                execs.append(payload)
            elif isinstance(ev, dict) and ev.get("type") == "tool.executed":
                execs.append(ev)
            elif isinstance(payload, dict) and "name" in payload and "status" in payload:
                execs.append(payload)
        return self._analyze(execs)

    def _in_window(self, ev: Dict[str, Any]) -> bool:
        """时间窗过滤: 无 window 配置则全量保留。"""
        if self._window_s is None:
            return True
        ts = ev.get("ts") or ev.get("elapsed_ts") or 0
        return bool(ts) and (time.time() - float(ts)) <= self._window_s

    def _analyze(self, execs: List[Dict[str, Any]]) -> List[Experience]:
        # (tool) → 计数 / (tool, error_type) → 计数
        failures: Counter = Counter()
        failure_types: Dict[str, str] = {}
        denied: Counter = Counter()
        slow: Counter = Counter()
        ok: Counter = Counter()

        for e in execs:
            if not self._in_window(e):
                continue
            name = e.get("name", "?")
            status = e.get("status", "ok")
            if status == "error":
                et = e.get("error_type")
                key: Tuple[str, str] = (name, et or "unknown")
                failures[key] += 1
                if et:
                    failure_types[name] = et
            elif status == "denied":
                denied[name] += 1
            elif status == "ok":
                ok[name] += 1
                if (e.get("elapsed") or 0) >= self.SLOW_THRESHOLD:
                    slow[name] += 1

        out: List[Experience] = []

        # 失败: 按 (工具, 错误类型) 聚类, 同源错误合并为一条
        for (name, et), cnt in failures.items():
            if cnt >= 1:
                out.append(Experience(
                    kind="failure", tool=name,
                    summary=f"工具 {name} 执行失败 {cnt} 次 (类型: {et})",
                    count=cnt,
                    detail={"error_type": et, "freq": cnt},
                ))

        for name, cnt in denied.items():
            if cnt >= self.MIN_FREQ:
                out.append(Experience(
                    kind="denied", tool=name,
                    summary=f"工具 {name} 被拒绝 {cnt} 次 (高频危险操作, 建议沉淀为规则)",
                    count=cnt, detail={"freq": cnt},
                ))
        for name, cnt in slow.items():
            if cnt >= self.MIN_FREQ:
                out.append(Experience(
                    kind="slow", tool=name,
                    summary=f"工具 {name} 慢调用 {cnt} 次 (>= {self.SLOW_THRESHOLD}s, 建议缓存/拆分)",
                    count=cnt, detail={"freq": cnt},
                ))
        for name, cnt in ok.items():
            if cnt >= self.OK_SKILL_THRESHOLD:
                out.append(Experience(
                    kind="repeated_ok", tool=name,
                    summary=f"工具 {name} 高频成功 {cnt} 次 (可提炼为技能流程)",
                    count=cnt, detail={"freq": cnt},
                ))
        return out
