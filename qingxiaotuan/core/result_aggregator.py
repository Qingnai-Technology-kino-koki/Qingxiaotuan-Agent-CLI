"""结果聚合器 (ResultAggregator) —— 多 Agent 协作的最终交付环节。

多个 Agent 并行执行后, 产物需要聚合:
1. **去重**: 相同/相似的结果合并;
2. **冲突检测**: 矛盾的结果标记出来;
3. **质量评估**: 对每个结果做质量打分;
4. **优先级合并**: 高质量结果优先;
5. **摘要生成**: 生成最终交付物的摘要;
6. **审计追溯**: 记录聚合决策的依据。
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ============================================================ 结果条目

@dataclass
class ResultEntry:
    """单个 Agent 的执行结果。"""

    task_id: str
    agent_id: str
    content: str
    role: str = ""
    quality_score: float = 0.0  # 0.0 ~ 1.0
    elapsed: float = 0.0
    tool_calls: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    _content_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self._content_hash:
            self._content_hash = hashlib.md5(
                self.content.encode("utf-8", errors="replace")
            ).hexdigest()

    @property
    def content_hash(self) -> str:
        return self._content_hash

    def similarity(self, other: "ResultEntry") -> float:
        """计算两个结果的相似度 (简单的 Jaccard 相似度)。"""
        if not self.content or not other.content:
            return 0.0
        # 用词级 Jaccard
        words_a = set(self.content.lower().split())
        words_b = set(other.content.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union) if union else 0.0


# ============================================================ 聚合决策

@dataclass
class AggregationDecision:
    """聚合过程中的一个决策。"""

    decision_type: str  # "dedup" | "conflict" | "priority" | "merge"
    description: str
    affected_tasks: List[str]
    details: Dict[str, Any] = field(default_factory=dict)


# ============================================================ 聚合结果

@dataclass
class AggregatedResult:
    """最终聚合结果。"""

    entries: List[ResultEntry]
    merged_content: str
    conflicts: List[Dict[str, Any]]
    decisions: List[AggregationDecision]
    quality_summary: Dict[str, float]
    elapsed: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_count": len(self.entries),
            "conflict_count": len(self.conflicts),
            "decision_count": len(self.decisions),
            "quality_summary": self.quality_summary,
            "merged_content_len": len(self.merged_content),
        }


# ============================================================ 结果聚合器

class ResultAggregator:
    """多 Agent 结果聚合器。

    用法:
        aggregator = ResultAggregator()
        aggregator.add_result("T1", "agent_a", "分析结果A...", quality_score=0.8)
        aggregator.add_result("T2", "agent_b", "分析结果B...", quality_score=0.9)
        result = aggregator.aggregate()
        print(result.merged_content)
    """

    def __init__(
        self,
        dedup_threshold: float = 0.85,
        conflict_threshold: float = 0.3,
    ) -> None:
        self._entries: List[ResultEntry] = []
        self._dedup_threshold = dedup_threshold
        self._conflict_threshold = conflict_threshold
        self._decisions: List[AggregationDecision] = []

    def add_result(
        self,
        task_id: str,
        agent_id: str,
        content: str,
        role: str = "",
        quality_score: float = 0.0,
        elapsed: float = 0.0,
        tool_calls: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """添加一个 Agent 的执行结果。"""
        entry = ResultEntry(
            task_id=task_id,
            agent_id=agent_id,
            content=content,
            role=role,
            quality_score=quality_score,
            elapsed=elapsed,
            tool_calls=tool_calls,
            metadata=metadata or {},
        )
        self._entries.append(entry)

    def aggregate(self) -> AggregatedResult:
        """执行聚合, 返回最终结果。"""
        started = time.time()
        self._decisions.clear()

        if not self._entries:
            return AggregatedResult(
                entries=[], merged_content="(无结果)",
                conflicts=[], decisions=[], quality_summary={},
            )

        # 1. 去重: 移除高度相似的结果
        deduped = self._deduplicate(self._entries)

        # 2. 冲突检测: 检查矛盾的结果
        conflicts = self._detect_conflicts(deduped)

        # 3. 质量评估
        quality_summary = self._assess_quality(deduped)

        # 4. 按质量和角色合并
        merged = self._merge_results(deduped, conflicts)

        elapsed = time.time() - started
        return AggregatedResult(
            entries=deduped,
            merged_content=merged,
            conflicts=conflicts,
            decisions=self._decisions,
            quality_summary=quality_summary,
            elapsed=elapsed,
        )

    def _deduplicate(self, entries: List[ResultEntry]) -> List[ResultEntry]:
        """去重: 移除高度相似的结果, 保留质量更高的。"""
        if len(entries) <= 1:
            return list(entries)

        keep: List[ResultEntry] = []
        removed: List[str] = []

        for entry in entries:
            is_dup = False
            for kept in keep:
                sim = entry.similarity(kept)
                if sim >= self._dedup_threshold:
                    # 相似度高: 保留质量更好的
                    if entry.quality_score > kept.quality_score:
                        # 替换
                        idx = keep.index(kept)
                        keep[idx] = entry
                        removed.append(kept.task_id)
                    else:
                        removed.append(entry.task_id)
                    is_dup = True
                    break
            if not is_dup:
                keep.append(entry)

        if removed:
            self._decisions.append(AggregationDecision(
                decision_type="dedup",
                description=f"去重: 移除了 {len(removed)} 个相似结果",
                affected_tasks=removed,
                details={"threshold": self._dedup_threshold},
            ))

        return keep

    def _detect_conflicts(self, entries: List[ResultEntry]) -> List[Dict[str, Any]]:
        """检测矛盾的结果。"""
        conflicts: List[Dict[str, Any]] = []

        # 简单的冲突检测: 检查结果中是否包含矛盾的结论
        # 例如: "应该用 X" vs "不应该用 X"
        negation_patterns = [
            (r"应该", r"不应该"),
            (r"建议", r"不建议"),
            (r"推荐", r"不推荐"),
            (r"可以", r"不可以"),
            (r"安全", r"不安全"),
            (r"正确", r"不正确"),
        ]

        for i, entry_a in enumerate(entries):
            for j, entry_b in enumerate(entries):
                if j <= i:
                    continue
                # 检查是否对同一主题有矛盾结论
                for pos_pattern, neg_pattern in negation_patterns:
                    a_has_pos = bool(re.search(pos_pattern, entry_a.content))
                    a_has_neg = bool(re.search(neg_pattern, entry_a.content))
                    b_has_pos = bool(re.search(pos_pattern, entry_b.content))
                    b_has_neg = bool(re.search(neg_pattern, entry_b.content))

                    if (a_has_pos and b_has_neg) or (a_has_neg and b_has_pos):
                        conflicts.append({
                            "task_a": entry_a.task_id,
                            "task_b": entry_b.task_id,
                            "type": "contradiction",
                            "pattern": f"{pos_pattern} vs {neg_pattern}",
                            "description": (
                                f"任务 {entry_a.task_id} 和 {entry_b.task_id} "
                                f"在 '{pos_pattern}' 上存在矛盾"
                            ),
                        })
                        break

        if conflicts:
            self._decisions.append(AggregationDecision(
                decision_type="conflict",
                description=f"检测到 {len(conflicts)} 个矛盾结果",
                affected_tasks=[c["task_a"] for c in conflicts],
                details={"conflicts": [c["description"] for c in conflicts]},
            ))

        return conflicts

    def _assess_quality(self, entries: List[ResultEntry]) -> Dict[str, float]:
        """评估每个结果的质量。"""
        quality: Dict[str, float] = {}
        for entry in entries:
            score = entry.quality_score
            # 基于内容质量自动评分 (如果未提供)
            if score == 0.0:
                score = self._auto_quality_score(entry)
            quality[entry.task_id] = score
        return quality

    def _auto_quality_score(self, entry: ResultEntry) -> float:
        """自动质量评分 (基于内容特征)。"""
        content = entry.content
        if not content:
            return 0.0

        score = 0.5  # 基础分

        # 长度适中加分
        length = len(content)
        if 100 < length < 10000:
            score += 0.1
        elif length > 10000:
            score += 0.05  # 太长可能有冗余

        # 包含具体信息加分
        if re.search(r'\d+\.\d+', content):  # 包含数字
            score += 0.05
        if '```' in content:  # 包含代码块
            score += 0.1
        if re.search(r'#{1,3}\s', content):  # 包含标题
            score += 0.05
        if re.search(r'[\u4e00-\u9fff]', content):  # 包含中文
            score += 0.05

        # 包含工具调用记录 (说明做了实际工作)
        if entry.tool_calls > 0:
            score += 0.1

        return min(1.0, score)

    def _merge_results(
        self,
        entries: List[ResultEntry],
        conflicts: List[Dict[str, Any]],
    ) -> str:
        """合并所有结果为最终交付物。"""
        if not entries:
            return "(无结果)"

        # 按角色分组
        by_role: Dict[str, List[ResultEntry]] = {}
        for entry in entries:
            role = entry.role or "unknown"
            by_role.setdefault(role, []).append(entry)

        parts: List[str] = []

        # 按角色顺序输出
        role_order = [
            "architect", "implementer", "reviewer", "tester",
            "debugger", "security_auditor", "documenter",
        ]
        for role in role_order:
            if role not in by_role:
                continue
            role_entries = by_role[role]
            # 按质量排序
            role_entries.sort(key=lambda e: -e.quality_score)
            for entry in role_entries:
                parts.append(f"### [{entry.role or role}] 任务 {entry.task_id}")
                parts.append(entry.content.strip())
                parts.append("")

        # 处理未在预定义顺序中的角色
        for role, role_entries in by_role.items():
            if role in role_order:
                continue
            for entry in role_entries:
                parts.append(f"### [{role}] 任务 {entry.task_id}")
                parts.append(entry.content.strip())
                parts.append("")

        # 附加冲突警告
        if conflicts:
            parts.append("## ⚠️ 注意: 以下结果存在矛盾")
            for c in conflicts:
                parts.append(f"- {c['description']}")

        return "\n\n".join(parts).strip()

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_entries": len(self._entries),
            "decisions": len(self._decisions),
        }
