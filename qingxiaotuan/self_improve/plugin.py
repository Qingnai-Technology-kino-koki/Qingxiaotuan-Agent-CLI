"""Self-Improve 内核插件 —— 把复盘/规则/技能/蒸馏四件套注册为内核服务。

激活后提供 "self_improve" 服务, 暴露:
  - summarize(): 读取内核事件流, 返回经验列表与将生成的规则/技能摘要 (dry-run, 不落盘)
  - apply():     把经验固化为 rules .jsonl + 技能草稿, 并回报落盘路径
  - distill():   触发一轮自动技能蒸馏闭环 (事件流 → 经验 → 技能 → 同步技能库)
  - feedback():  蒸馏技能使用效果反馈 (成功转正 / 失败降级)
  - query_rule(): 供 ToolRegistry 分发前实时查询 learned 护栏规则

闭环接线 (对标 CC 动态技能):
  事件流 → Reflector(经验) → RuleGenerator(规则, 即时生效)
                            → SkillGenerator(草稿, 人工审阅)
                            → AutoDistiller(蒸馏, 自动激活/同步 SkillManager) ← Feedback

可审计、可重放、不失控: 规则需经 rules 引擎显式 load, 技能草稿默认人工审阅,
蒸馏技能可配置 auto_activate 决定是否自动注入系统提示。
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config.loader import home_dir
from ..core.kernel import Kernel, Plugin
from .reflector import Reflector, Experience
from .rulegen import RuleGenerator
from .skillgen import SkillGenerator
from .store import SelfImproveRuleStore

log = logging.getLogger("qingxiaotuan.self_improve.plugin")


def _legacy_learned_dir() -> Path:
    """旧版默认位置: 源码树内的 qingxiaotuan/skills/learned (运行时状态不应进源码树)。"""
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return Path(base) / "qingxiaotuan" / "skills" / "learned"


def _migrate_legacy(legacy: Path, target: Path) -> None:
    """一次性把旧位置的 learned 数据搬入 <home>/skills/learned。

    仅复制目标缺失的条目 (不覆盖新数据), 任一步失败都静默跳过 —— 迁移不能阻塞启动。
    """
    if not legacy.is_dir() or legacy == target:
        return
    try:
        target.mkdir(parents=True, exist_ok=True)
        src_rules = legacy / "self_improve.jsonl"
        if src_rules.exists() and not (target / "self_improve.jsonl").exists():
            shutil.copy2(src_rules, target / "self_improve.jsonl")
        for child in legacy.iterdir():
            dst = target / child.name
            if child.is_dir() and child.name.startswith("learned-") and not dst.exists():
                shutil.copytree(child, dst)
    except OSError:
        pass


def _build_distiller(cfg: Any, home: str, kernel: Kernel) -> Optional[Any]:
    """按内核装配状态构建 AutoDistiller; 任何失败都返回 None, 不阻塞插件激活。"""
    try:
        from .auto_distiller import AutoDistiller
        return AutoDistiller(
            session_store=kernel.get("session_store"),
            skill_manager=kernel.get("skill_manager"),
            config=cfg,
            home=home,
        )
    except Exception:
        log.warning("AutoDistiller 装配失败, 蒸馏闭环不可用", exc_info=True)
        return None


class SelfImprovePlugin(Plugin):
    name = "self.improve"
    provides = ["self_improve", "self_improve_rules"]
    requires = ["config"]

    def activate(self, kernel: Kernel) -> None:
        cfg = kernel.get("config")
        # learned 规则/技能草稿是运行时状态, 统一放 <QXT_HOME>/skills/learned, 不落源码树
        home = Path(getattr(cfg, "home", None) or home_dir())
        learned_dir = str(home / "skills" / "learned")
        _migrate_legacy(_legacy_learned_dir(), Path(learned_dir))
        get = cfg.get if cfg is not None else (lambda key, default=None: default)
        skills_dir = get("self_improve.skills_dir") or learned_dir
        rules_dir = get("self_improve.rules_dir") or learned_dir
        rules_path = os.path.join(rules_dir, "self_improve.jsonl")
        reflector = Reflector(kernel)
        store = SelfImproveRuleStore(rules_path)
        store.load()  # 启动时加载已有 learned 规则, 立即生效
        distiller = _build_distiller(cfg, str(home), kernel)
        self._svc = SelfImproveService(kernel, reflector, rules_dir, skills_dir, store,
                                       distiller=distiller)
        kernel.provide("self_improve", self._svc, owner=self.name)
        kernel.provide("self_improve_rules", store, owner=self.name)

    def deactivate(self, kernel: Kernel) -> None:
        kernel.unprovide("self_improve")
        kernel.unprovide("self_improve_rules")


class SelfImproveService:
    def __init__(self, kernel: Kernel, reflector: Reflector, rules_dir: str, skills_dir: str,
                 store: SelfImproveRuleStore, distiller: Optional[Any] = None) -> None:
        self._kernel = kernel
        self._reflector = reflector
        self._rules_dir = rules_dir
        self._skills_dir = skills_dir
        self._store = store
        self._distiller = distiller

    # ------------------------------------------------------------ 复盘 (dry-run)

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

    # ------------------------------------------------------------ 固化 (apply)

    def apply(self) -> Dict[str, Any]:
        exps: List[Experience] = self._reflector.reflect()
        rg = RuleGenerator(self._rules_dir)
        sg = SkillGenerator(self._skills_dir)
        rules = rg.generate(exps)
        # 落盘并即时加载到 store —— 形成"复盘→规则→实时闭环"
        rule_path = self._store.write(rules) if rules else None
        draft_paths = sg.draft(exps)
        result: Dict[str, Any] = {
            "rules_written": rule_path,
            "rules_count": len(rules),
            "rules_loaded": self._store.count(),
            "skill_drafts": draft_paths,
            "experiences": len(exps),
        }
        # 规则固化后顺带跑一轮蒸馏, 让"经验→可用技能"闭环同批落成
        if self._distiller is not None:
            result["distilled"] = self.distill()
        return result

    # ------------------------------------------------------------ 蒸馏闭环

    def distill(self) -> Dict[str, Any]:
        """触发一轮自动技能蒸馏: 事件流 → 经验 → 技能 → 同步 SkillManager。"""
        if self._distiller is None:
            return {"enabled": False, "new_skills": [], "reason": "distiller unavailable"}
        try:
            new = self._distiller.distill()
            st = self._distiller.stats()
            return {
                "enabled": True,
                "new_skills": [s.name for s in new],
                "total_skills": st.get("total_skills", 0),
                "active_skills": st.get("active_skills", 0),
            }
        except Exception:
            log.warning("自动蒸馏失败", exc_info=True)
            return {"enabled": True, "new_skills": [], "error": "distill failed"}

    def feedback(self, skill_name: str, success: bool) -> bool:
        """蒸馏技能使用效果反馈: 成功计数上修/草稿转正, 失败计数下修/降级。"""
        if self._distiller is None:
            return False
        try:
            self._distiller.feedback(skill_name, success)
            return True
        except Exception:
            return False

    def distilled_skills(self) -> List[Dict[str, Any]]:
        """列出当前所有蒸馏技能 (含 draft)。"""
        if self._distiller is None:
            return []
        return self._distiller.list_skills()

    # ------------------------------------------------------------ 规则查询

    def query_rule(self, tool_name: str, args: dict) -> dict | None:
        """供 ToolRegistry 分发前实时查询 learned 护栏规则。"""
        return self._store.query(tool_name, args)
