"""规则生成器 (RuleGenerator) —— 把复盘经验固化为 rules 引擎可加载的规则。

生成的规则写入 skills 目录下的 .jsonl (每行一条 JSON 规则),
可被 rules 引擎 load 后用于后续执行的 lint/check。
每条规则带 meta.source = "self-improve", 便于审计与回滚。

规则类型映射:
  - denied 经验  -> guard 规则: 当命令匹配该工具的危险模式时, severity=high, 必须人工确认
  - failure 经验 -> lint 规则: 标记易失败的调用模式, severity=medium
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from .reflector import Experience


class RuleGenerator:
    def __init__(self, rules_dir: str) -> None:
        self.rules_dir = rules_dir
        os.makedirs(rules_dir, exist_ok=True)

    def generate(self, exps: List[Experience]) -> List[Dict[str, Any]]:
        """把经验转为规则 dict 列表 (尚未落盘)。"""
        rules: List[Dict[str, Any]] = []
        for exp in exps:
            if exp.kind == "denied":
                rules.append(self._guard_rule(exp))
            elif exp.kind == "failure":
                rules.append(self._lint_rule(exp))
            # slow / repeated_ok 不生成阻断规则, 留给 skillgen
        return rules

    def _guard_rule(self, exp: Experience) -> Dict[str, Any]:
        return {
            "id": f"self-improve-guard-{exp.tool}",
            "severity": "high",
            "action": "confirm",   # self-improve 只升级为"需人工确认", 不直接硬阻断
            "match": {"tool": exp.tool},
            "message": f"该操作历史上被拒绝 {exp.count} 次, 需人工确认",
            "meta": {"source": "self-improve", "generated_at": datetime.now().isoformat(timespec="seconds")},
        }

    def _lint_rule(self, exp: Experience) -> Dict[str, Any]:
        et = exp.detail.get("error_type", "unknown")
        return {
            "id": f"self-improve-lint-{exp.tool}",
            "severity": "medium",
            "action": "confirm",
            "match": {"tool": exp.tool},
            "message": f"该工具曾因 {et} 失败 {exp.count} 次, 调用前请校验参数",
            "meta": {"source": "self-improve", "generated_at": datetime.now().isoformat(timespec="seconds")},
        }

    def write(self, rules: List[Dict[str, Any]], fname: str = "self_improve.jsonl") -> str:
        """落盘为 .jsonl, 返回文件路径。"""
        path = os.path.join(self.rules_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            for r in rules:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return path

    def summarize(self, rules: List[Dict[str, Any]]) -> str:
        if not rules:
            return "（无新规则生成）"
        lines = [f"将生成 {len(rules)} 条规则:"]
        for r in rules:
            lines.append(f"  - [{r['severity']}] {r['id']}: {r.get('message','')}")
        return "\n".join(lines)
