"""Self-Improve 内核插件 —— 把复盘/规则/技能三件套注册为内核服务。

激活后提供 "self_improve" 服务, 暴露:
  - summarize(): 读取内核事件流, 返回经验列表与将生成的规则/技能摘要 (dry-run, 不落盘)
  - apply():     把经验固化为 rules .jsonl + 技能草稿, 并回报落盘路径

不自动修改任何运行中的行为: 生成的规则需经 rules 引擎显式 load, 技能草稿需人工审阅。
这正是「可审计、可重放、不失控」的世界级护栏哲学。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from ..core.kernel import Kernel, Plugin
from .reflector import Reflector, Experience
from .rulegen import RuleGenerator
from .skillgen import SkillGenerator
from .store import SelfImproveRuleStore


class SelfImprovePlugin(Plugin):
    name = "self.improve"
    provides = ["self_improve", "self_improve_rules"]
    requires = ["config"]

    def activate(self, kernel: Kernel) -> None:
        cfg = kernel.get("config") or {}
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        skills_dir = cfg.get("self_improve.skills_dir") or os.path.join(base, "qingxiaotuan", "skills", "learned")
        rules_dir = cfg.get("self_improve.rules_dir") or os.path.join(base, "qingxiaotuan", "skills", "learned")
        rules_path = os.path.join(rules_dir, "self_improve.jsonl")
        reflector = Reflector(kernel)
        store = SelfImproveRuleStore(rules_path)
        store.load()  # 启动时加载已有 learned 规则, 立即生效
        self._svc = SelfImproveService(kernel, reflector, rules_dir, skills_dir, store)
        kernel.provide("self_improve", self._svc, owner=self.name)
        kernel.provide("self_improve_rules", store, owner=self.name)

    def deactivate(self, kernel: Kernel) -> None:
        kernel.unprovide("self_improve")
        kernel.unprovide("self_improve_rules")


class SelfImproveService:
    def __init__(self, kernel: Kernel, reflector: Reflector, rules_dir: str, skills_dir: str,
                 store: SelfImproveRuleStore) -> None:
        self._kernel = kernel
        self._reflector = reflector
        self._rules_dir = rules_dir
        self._skills_dir = skills_dir
        self._store = store

    def summarize(self) -> Dict[str, Any]:
        exps: List[Experience] = self._reflector.reflect()
        rg = RuleGenerator(self._rules_dir)
        sg = SkillGenerator(self._skills_dir)
        rules = rg.generate(exps)
        drafts = [os.path.basename(os.path.dirname(p)) for p in sg.draft([])]  # 不落盘, 仅统计
        return {
            "experiences": [e.to_dict() for e in exps],
            "rule_preview": rg.summarize(rules),
            "skill_draft_candidates": [e.tool for e in exps if e.kind == "repeated_ok"],
            "total_experiences": len(exps),
        }

    def apply(self) -> Dict[str, Any]:
        exps: List[Experience] = self._reflector.reflect()
        rg = RuleGenerator(self._rules_dir)
        sg = SkillGenerator(self._skills_dir)
        rules = rg.generate(exps)
        # 落盘并即时加载到 store —— 形成"复盘→规则→实时闭环"
        rule_path = self._store.write(rules) if rules else None
        draft_paths = sg.draft(exps)
        return {
            "rules_written": rule_path,
            "rules_count": len(rules),
            "rules_loaded": self._store.count(),
            "skill_drafts": draft_paths,
            "experiences": len(exps),
        }

    def query_rule(self, tool_name: str, args: dict) -> dict | None:
        """供 ToolRegistry 分发前实时查询 learned 护栏规则。"""
        return self._store.query(tool_name, args)
