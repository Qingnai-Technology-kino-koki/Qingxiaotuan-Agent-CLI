"""复盘器 (Reflector) —— 从内核事件流抽取可改进经验。

每个工具执行都会 emit `tool.executed` 事件 (见 tools/base.py)。
复盘器读取内核事件流 (append-only, kernel.events), 产出结构化 Experience:
  - failure:    执行报错 (error_type + 工具名 + 频次)
  - denied:     用户/策略拒绝的危险操作 (高频项需沉淀为规则)
  - slow:       慢操作 (elapsed 超阈值, 建议缓存/拆分)
  - repeated_ok: 某工具高频成功 (可提炼为技能)

这些 Experience 是「自我改进」的原料。
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    """从内核事件流 (kernel.events) 抽取经验。"""

    SLOW_THRESHOLD = 5.0          # 秒, 超过视为慢操作
    MIN_FREQ = 2                  # 至少出现这么多次才值得沉淀

    def __init__(self, kernel: Any = None) -> None:
        self._kernel = kernel

    def reflect(self, events: Optional[List[Any]] = None) -> List[Experience]:
        """从给定事件列表 (缺省取内核事件流) 抽取经验。"""
        if events is None:
            events = list(self._kernel.events) if self._kernel is not None else []
        # 只看 tool.executed
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

    def _analyze(self, execs: List[Dict[str, Any]]) -> List[Experience]:
        failures: Counter = Counter()
        failure_types: Dict[str, str] = {}
        denied: Counter = Counter()
        slow: Counter = Counter()
        ok: Counter = Counter()

        for e in execs:
            name = e.get("name", "?")
            status = e.get("status", "ok")
            if status == "error":
                failures[name] += 1
                if e.get("error_type"):
                    failure_types[name] = e["error_type"]
            elif status == "denied":
                denied[name] += 1
            elif status == "ok":
                ok[name] += 1
                if (e.get("elapsed") or 0) >= self.SLOW_THRESHOLD:
                    slow[name] += 1

        out: List[Experience] = []

        for name, cnt in failures.items():
            if cnt >= 1:
                out.append(Experience(
                    kind="failure", tool=name,
                    summary=f"工具 {name} 执行失败 {cnt} 次 (类型: {failure_types.get(name, '未知')})",
                    count=cnt,
                    detail={"error_type": failure_types.get(name), "freq": cnt},
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
            if cnt >= 5:
                out.append(Experience(
                    kind="repeated_ok", tool=name,
                    summary=f"工具 {name} 高频成功 {cnt} 次 (可提炼为技能流程)",
                    count=cnt, detail={"freq": cnt},
                ))
        return out
