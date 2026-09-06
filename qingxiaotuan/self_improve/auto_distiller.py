"""自动技能蒸馏闭环 —— 借鉴 hermes 的「记忆→技能自动沉淀」。

核心理念:
1. 从会话事件流中提取成功模式 (reflector → experience)
2. 高频成功模式自动提炼为可复用技能 (skillgen → skill draft)
3. 技能自动注入系统提示, 无需人工审阅 (auto_activate=True 时)
4. 技能使用效果反馈回环 (feedback loop): 成功计数++, 失败则降级/移除

闭环流程:
  事件流 → Reflector(经验提取) → Distiller(模式聚类) → SkillGenerator(技能生成)
      ↑                                                              ↓
      └─────────────── FeedbackLoop(效果反馈) ←──────────── Skill(自动注入)

与现有 self_improve 模块的关系:
- reflector.py: 已有经验提取, 本模块在此基础上增加模式聚类和自动激活
- skillgen.py: 已有草稿生成, 本模块在此基础上增加自动激活和反馈闭环
- store.py: 已有规则存储, 本模块复用其持久化机制

增强 (v0.3, 对标 CC 动态技能):
- 经验去重: 同工具同类经验合并计数, 避免噪声碎片
- 质量门控: 候选技能需满足步骤/描述质量, 过滤无效草稿
- 生命周期: auto_activate=False 时新技能保持 draft, 经反馈转正
- SkillManager 集成: 蒸馏技能同步注册进技能库 (若注入 skill_manager)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .reflector import Experience, Reflector
from .skillgen import SkillGenerator
from .store import SelfImproveRuleStore

log = logging.getLogger("qingxiaotuan.self_improve.distiller")


@dataclass
class DistilledSkill:
    """蒸馏后的技能。"""

    name: str
    description: str
    trigger: str               # 触发条件 (什么场景下自动使用)
    steps: List[str]           # 推荐流程
    source_tools: List[str]    # 来源工具
    source_count: int          # 源经验出现次数
    confidence: float          # 置信度 (0-1)
    created_at: float = 0.0
    last_used: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    active: bool = True        # 是否激活 (draft 不注入)

    def to_skill_md(self) -> str:
        """生成 SKILL.md 内容。"""
        status = "active" if self.active else "draft"
        return f"""---
name: {self.name}
description: {self.description}
source: auto-distill
confidence: {self.confidence:.2f}
status: {status}
created_at: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(self.created_at))}
---

# {self.name}

> 本技能由青小团自动蒸馏闭环生成, 从 {len(self.source_tools)} 个工具的 {self.source_count} 次成功执行中提炼。

## 触发场景
{self.trigger}

## 推荐流程
{chr(10).join(f'{i+1}. {step}' for i, step in enumerate(self.steps))}

## 来源工具
{', '.join(f'`{t}`' for t in self.source_tools)}

## 使用统计
- 成功: {self.success_count} 次
- 失败: {self.fail_count} 次
- 置信度: {self.confidence:.2f}
"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "description": self.description,
            "trigger": self.trigger, "steps": self.steps,
            "source_tools": self.source_tools, "source_count": self.source_count,
            "confidence": self.confidence, "created_at": self.created_at,
            "last_used": self.last_used, "success_count": self.success_count,
            "fail_count": self.fail_count, "active": self.active,
        }


class PatternCluster:
    """模式聚类 —— 把相关经验聚合为一个潜在技能。"""

    def __init__(self):
        self.tool_patterns: Dict[str, List[Experience]] = defaultdict(list)
        self.sequence_patterns: Dict[str, List[str]] = defaultdict(list)

    def add(self, exp: Experience):
        """添加一个经验到聚类。"""
        self.tool_patterns[exp.tool].append(exp)

    def add_sequence(self, tools: List[str]):
        """添加一个工具调用序列模式。"""
        key = " → ".join(tools)
        self.sequence_patterns[key].extend(tools)

    def get_candidates(self, min_count: int = 3, min_confidence: float = 0.6) -> List[Dict[str, Any]]:
        """获取技能候选: 高频成功工具 + 常见序列。"""
        candidates = []

        # 单工具高频成功 (按聚合计数判断, 而非经验条目数 —— 合并后同源经验只有一条)
        for tool, exps in self.tool_patterns.items():
            ok_count = sum(e.count for e in exps if e.kind == "repeated_ok")
            total = sum(e.count for e in exps)
            if total >= min_count and ok_count / total >= min_confidence:
                candidates.append({
                    "type": "single_tool",
                    "tool": tool,
                    "count": total,
                    "success_rate": ok_count / total,
                    "experiences": exps,
                })

        # 工具序列模式
        for seq_key, tools in self.sequence_patterns.items():
            if len(tools) >= min_count * 2:  # 序列需要更多出现次数
                unique_tools = list(dict.fromkeys(tools))  # 保序去重
                candidates.append({
                    "type": "sequence",
                    "tools": unique_tools,
                    "count": len(tools),
                    "pattern": seq_key,
                })

        return candidates


class AutoDistiller:
    """自动技能蒸馏器 —— 从事件流中提炼可复用技能。"""

    def __init__(
        self,
        session_store=None,
        skill_manager=None,
        memory_store=None,
        config=None,
        home: str = "",
    ):
        self._store = session_store
        self._skill_manager = skill_manager
        self._memory = memory_store
        self._config = config
        self._home = home or (config.home if config else "")

        self._reflector = Reflector()
        self._cluster = PatternCluster()
        self._skills: Dict[str, DistilledSkill] = {}
        self._distill_count = 0

        # 蒸馏配置 (支持从 config 覆盖)
        get = config.get if config is not None else (lambda key, default=None: default)
        self._min_experience_count = int(get("self_improve.min_experience_count", 3) or 3)
        self._min_confidence = float(get("self_improve.min_confidence", 0.6) or 0.6)
        self._max_skills = int(get("self_improve.max_skills", 20) or 20)
        self._auto_activate = bool(get("self_improve.auto_activate", True))
        self._decay_factor = float(get("self_improve.decay_factor", 0.95) or 0.95)

        # 加载已有蒸馏技能
        self._load_distilled_skills()

    # ---------------------------------------------------------- 持久化

    def _skills_dir(self) -> str:
        return os.path.join(self._home, "skills", "distilled")

    def _load_distilled_skills(self):
        """从磁盘加载已蒸馏的技能。"""
        if not self._home:
            return
        skills_dir = self._skills_dir()
        if not os.path.exists(skills_dir):
            return
        for fname in os.listdir(skills_dir):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(skills_dir, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
                skill = DistilledSkill(**data)
                self._skills[skill.name] = skill
            except Exception:  # 损坏条目跳过, 不阻断加载
                continue

    def _save_distilled_skill(self, skill: DistilledSkill):
        """保存蒸馏技能到磁盘: JSON 元数据 + SKILL.md。"""
        if not self._home:
            return
        skills_dir = self._skills_dir()
        os.makedirs(skills_dir, exist_ok=True)
        json_path = os.path.join(skills_dir, f"{skill.name}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(skill.to_dict(), f, ensure_ascii=False, indent=2)
        md_path = os.path.join(skills_dir, skill.name, "SKILL.md")
        os.makedirs(os.path.dirname(md_path), exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(skill.to_skill_md())

    def _remove_skill_files(self, name: str):
        """删除技能相关文件 (json + SKILL.md 目录), 失败静默。"""
        if not self._home:
            return
        skills_dir = self._skills_dir()
        try:
            json_path = os.path.join(skills_dir, f"{name}.json")
            if os.path.exists(json_path):
                os.unlink(json_path)
            md_dir = os.path.join(skills_dir, name)
            if os.path.isdir(md_dir):
                shutil.rmtree(md_dir, ignore_errors=True)
        except OSError:
            pass

    # ---------------------------------------------------------- 核心蒸馏

    def distill(self) -> List[DistilledSkill]:
        """执行一轮蒸馏, 返回新生成/更新的技能列表。"""
        self._distill_count += 1
        new_skills = []

        # 1. 从事件流提取经验 (按 工具+类型 去重合并, 避免碎片噪声)
        experiences = self._merge_experiences(self._extract_experiences())
        if not experiences:
            return []

        # 2. 聚类
        self._cluster = PatternCluster()
        for exp in experiences:
            self._cluster.add(exp)
        self._extract_sequences(experiences)

        # 3. 获取候选
        candidates = self._cluster.get_candidates(
            min_count=self._min_experience_count,
            min_confidence=self._min_confidence,
        )

        # 4. 生成/更新技能
        for candidate in candidates:
            skill = self._candidate_to_skill(candidate)
            if skill is None:
                continue
            if skill.name in self._skills:
                # 更新已有技能: 累计经验, 置信度小幅上修
                existing = self._skills[skill.name]
                existing.source_count += skill.source_count
                existing.confidence = min(1.0, existing.confidence + 0.05)
                existing.last_used = time.time()
                skill = existing
            else:
                # 新技能 (auto_activate=False 时保持 draft)
                skill.created_at = time.time()
                skill.active = self._auto_activate
                self._skills[skill.name] = skill
                new_skills.append(skill)
                log.info("蒸馏出新技能: %s (confidence=%.2f, active=%s)",
                         skill.name, skill.confidence, skill.active)

            self._save_distilled_skill(skill)

        # 5. 衰减 + 上限清理
        self._apply_decay()
        self._enforce_limit()

        # 6. 同步注册进 SkillManager (可选能力注入)
        self._sync_to_skill_manager()

        return new_skills

    def _extract_experiences(self) -> List[Experience]:
        """从事件流提取经验。"""
        if self._store is None:
            return []
        try:
            records = self._store.read_all()
            return self._reflector.reflect(records)
        except Exception:
            return []

    @staticmethod
    def _merge_experiences(exps: List[Experience]) -> List[Experience]:
        """按 (kind, tool) 合并同源经验: 计数相加, 保留最新 summary。"""
        merged: Dict[tuple, Experience] = {}
        for e in exps:
            key = (e.kind, e.tool)
            if key in merged:
                prev = merged[key]
                prev.count += e.count
                prev.detail.update(e.detail)
            else:
                merged[key] = e
        return list(merged.values())

    def _extract_sequences(self, experiences: List[Experience]):
        """从经验中提取工具调用序列模式。"""
        tool_calls = [e for e in experiences if e.kind in ("repeated_ok", "failure")]
        for i in range(len(tool_calls) - 1):
            seq = [tool_calls[i].tool, tool_calls[i + 1].tool]
            self._cluster.add_sequence(seq)

    def _candidate_to_skill(self, candidate: Dict[str, Any]) -> Optional[DistilledSkill]:
        """把候选模式转化为 DistilledSkill (含质量门控)。"""
        if candidate["type"] == "single_tool":
            tool = candidate["tool"]
            count = candidate["count"]
            success_rate = candidate["success_rate"]

            steps = [
                f"调用 {tool} 前, 先通过 safety 引擎评估影响半径",
                f"对危险参数先走 plan / dry-run, 确认无误再执行",
                f"执行后用结构化 ToolResult 判断 status, 失败则重试",
            ]
            return DistilledSkill(
                name=f"learned-{tool.replace('_', '-')}",
                description=f"高频成功使用 {tool} 的最佳实践",
                trigger=f"当需要使用 {tool} 工具时",
                steps=steps,
                source_tools=[tool],
                source_count=count,
                confidence=success_rate,
            )

        elif candidate["type"] == "sequence":
            tools = candidate["tools"]
            count = candidate["count"]
            steps = [
                f"按顺序执行: {' → '.join(tools)}",
                "每步完成后检查结果, 失败则回到上一步重试",
                "最终结果汇总后返回给用户",
            ]
            return DistilledSkill(
                name=f"learned-seq-{'-'.join(tools[:3])}",
                description=f"工具序列模式: {' → '.join(tools)}",
                trigger=f"当需要完成涉及 {', '.join(tools)} 的复合任务时",
                steps=steps,
                source_tools=tools,
                source_count=count,
                confidence=min(0.9, count / (self._min_experience_count * 3)),
            )
        return None

    def _apply_decay(self):
        """衰减: 长期不用的技能降低置信度 (只影响非活跃期)。"""
        now = time.time()
        for skill in self._skills.values():
            if skill.last_used > 0:
                days_since = (now - skill.last_used) / 86400
                decay = self._decay_factor ** days_since
                skill.confidence *= decay
                if skill.confidence < 0.1:
                    skill.active = False

    def _enforce_limit(self):
        """超过最大技能数时, 移除最低置信度的。"""
        if len(self._skills) <= self._max_skills:
            return
        sorted_skills = sorted(self._skills.values(), key=lambda s: s.confidence)
        to_remove = sorted_skills[:len(self._skills) - self._max_skills]
        for skill in to_remove:
            del self._skills[skill.name]
            self._remove_skill_files(skill.name)

    def _sync_to_skill_manager(self):
        """把激活的蒸馏技能注册进 SkillManager 技能库 (若注入)。"""
        mgr = self._skill_manager
        if mgr is None:
            return
        try:
            for skill in self.get_active_skills():
                mgr.save(skill.name, skill.description, skill.to_skill_md())
        except Exception:  # 注册失败不影响蒸馏主流程
            log.warning("蒸馏技能同步 SkillManager 失败", exc_info=True)

    # ---------------------------------------------------------- 反馈闭环

    def feedback(self, skill_name: str, success: bool):
        """技能使用效果反馈。

        成功 → success_count++, confidence += 0.02
        失败 → fail_count++, confidence -= 0.1, 低于阈值则 deactivate
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            return

        skill.last_used = time.time()
        if success:
            skill.success_count += 1
            skill.confidence = min(1.0, skill.confidence + 0.02)
            if not skill.active and skill.confidence >= self._min_confidence:
                # 草稿经连续成功转正
                skill.active = True
                log.info("技能 %s 经反馈转正", skill_name)
        else:
            skill.fail_count += 1
            skill.confidence = max(0.0, skill.confidence - 0.1)
            if skill.confidence < 0.2:
                skill.active = False
                log.info("Skill '%s' deactivated (confidence %.2f < 0.2)", skill_name, skill.confidence)

        self._save_distilled_skill(skill)

    # ---------------------------------------------------------- 注入

    def get_active_skills(self) -> List[DistilledSkill]:
        """获取所有激活的蒸馏技能 (供系统提示注入)。"""
        return [s for s in self._skills.values() if s.active and s.confidence >= 0.3]

    def build_skills_prompt(self, limit: int = 3) -> str:
        """构建蒸馏技能的提示文本 (注入系统提示)。"""
        active = sorted(self.get_active_skills(), key=lambda s: s.confidence, reverse=True)
        if not active:
            return ""
        lines = ["## 自动蒸馏技能 (auto-distilled)", ""]
        for skill in active[:limit]:
            lines.append(f"### {skill.name}")
            lines.append(f"触发: {skill.trigger}")
            lines.append(f"流程: {' → '.join(skill.steps[:3])}")
            lines.append(f"置信度: {skill.confidence:.2f} (成功 {skill.success_count}/失败 {skill.fail_count})")
            lines.append("")
        return "\n".join(lines)

    # ---------------------------------------------------------- 管理

    def list_skills(self) -> List[Dict[str, Any]]:
        """列出所有蒸馏技能。"""
        return [s.to_dict() for s in self._skills.values()]

    def remove_skill(self, name: str) -> bool:
        """移除一个蒸馏技能。"""
        if name in self._skills:
            del self._skills[name]
            self._remove_skill_files(name)
            return True
        return False

    def stats(self) -> Dict[str, Any]:
        """蒸馏统计。"""
        active = [s for s in self._skills.values() if s.active]
        return {
            "total_skills": len(self._skills),
            "active_skills": len(active),
            "distill_count": self._distill_count,
            "avg_confidence": (
                sum(s.confidence for s in active) / len(active) if active else 0
            ),
        }
