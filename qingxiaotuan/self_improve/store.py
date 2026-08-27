"""Self-Improve 规则存储 —— 把 learned 规则落地为可实时查询的护栏。

设计哲学 (对标世界级 Agent 的可审计闭环):
    self-improve 复盘生成的规则, 不应默默生效、也不应立即硬阻断一切。
    本存储把规则落盘为 JSONL (带 meta.source=self-improve), 并暴露查询接口,
    供 ToolRegistry 在分发前查询: 命中 learned-guard 的工具调用, 自动升级为
    "必须人工确认" (而非直接 deny), 把控制权留给人类 —— 真正的「最小影响半径」。

规则结构 (与 rulegen.py 对齐, 额外增加 match 维度):
    {
      "id": "self-improve-guard-<tool>",
      "severity": "high" | "medium",
      "match": {"tool": "<tool_name>"} | {"tool": "<tool>", "pattern": "<substr>"},
      "action": "confirm" | "block",     # self-improve 只产出 confirm, block 留给人工升级
      "message": "...",
      "meta": {"source": "self-improve", "generated_at": "..."}
    }
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional


class SelfImproveRuleStore:
    """learned 规则的读写与实时查询。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._rules: List[Dict[str, Any]] = []
        self._loaded = False

    # ---------------------------------------------------------- 落盘/加载
    def write(self, rules: List[Dict[str, Any]], fname: str = "self_improve.jsonl") -> str:
        """把规则原子写入 JSONL (临时文件 + os.replace, 崩溃不留下半截文件)。"""
        path = os.path.join(os.path.dirname(self._path), fname) if os.path.isdir(self._path) \
            else self._path
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp",
                                         dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for r in rules:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        self._path = path
        self._rules = list(rules)
        self._loaded = True
        return path

    def load(self, path: Optional[str] = None) -> int:
        """加载 jsonl 规则, 返回规则条数。"""
        p = path or self._path
        if not p or not os.path.exists(p):
            self._rules = []
            self._loaded = True
            return 0
        rules: List[Dict[str, Any]] = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rules.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        self._rules = rules
        self._path = p
        self._loaded = True
        return len(rules)

    # ---------------------------------------------------------- 查询
    def query(self, tool_name: str, args: Dict[str, Any] | str) -> Optional[Dict[str, Any]]:
        """查询是否命中 learned 护栏规则。

        返回命中的规则 dict (含 action/message), 未命中返回 None。
        匹配维度:
          - match.tool 精确匹配工具名
          - 若 match.pattern 存在, 还要求参数序列化文本包含该子串
        """
        if not self._loaded:
            self.load()
        text = ""
        if isinstance(args, str):
            text = args
        else:
            try:
                text = json.dumps(args, ensure_ascii=False)
            except Exception:
                text = str(args)
        for r in self._rules:
            m = r.get("match") or {}
            if m.get("tool") != tool_name:
                continue
            pat = m.get("pattern")
            if pat and pat not in text:
                continue
            return r
        return None

    @property
    def rules(self) -> List[Dict[str, Any]]:
        if not self._loaded:
            self.load()
        return list(self._rules)

    def count(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._rules)
