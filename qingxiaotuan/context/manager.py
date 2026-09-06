"""上下文管理器 —— 借鉴 Claude Code 的长会话上下文优先级管理。

核心思想 (append-only 历史 + 智能压缩):
- 系统提示 (含钉死的代码库地图) 永远不动。
- 最近的 keep_recent 条消息永远保留 (近期上下文最宝贵)。
- 中间的旧历史在超过 token 预算时, 用模型做"无损摘要"折叠, 关键信息不丢。
- 折叠是懒惰的: 只在真正超预算时发生, 并且优先丢最旧的。

这样即使连续开发几十轮, Agent 仍能:
1. 始终"看得见"整个仓库结构 (系统提示里的地图);
2. 记得刚刚改了什么 (最近消息);
3. 记得更早期的决策与结论 (摘要)。

增强 (v0.4, 对标 CC 上下文质量):
- token 估算 lru 缓存: 长会话热路径反复估算同一批消息, 命中即省。
- 冗余去重: 压缩前先折叠连续重复消息 (不含 tool 结果, 不破坏协议)。
- 语义感知压缩: 融合路径传入消息重要性评分, 压缩质量指标回写统计。
- 压缩统计: stats() 暴露压缩次数/折叠条数/压缩率/信号保留率, 供观测与调参。
"""

from __future__ import annotations

import functools
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .compaction_fusion import (
    compact_head_tail_elision,
    compaction_metrics,
    message_importance,
)


@functools.lru_cache(maxsize=8192)
def estimate_tokens(text: str) -> int:
    """粗略估算 token 数: 英文约 4 字符/token, 中文约 1.6 字符/token, 取折中 ~3.2。"""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return int(cjk / 1.6 + other / 4)


def estimate_messages(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(str(p) for p in content)
        total += estimate_tokens(str(content))
        for tc in m.get("tool_calls", []) or []:
            total += estimate_tokens(str(tc))
    return total


def _same_content(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """两消息是否「内容相同」: role + content + tool_calls 形状一致 (tool 结果绝不合并)。"""
    if a.get("role") != b.get("role") or a.get("role") == "tool":
        return False
    if (a.get("content") or "") != (b.get("content") or ""):
        return False
    return (a.get("tool_calls") or []) == (b.get("tool_calls") or [])


class ContextManager:
    """在 Agent 主循环里被调用, 负责按需压缩历史。"""

    def __init__(
        self,
        keep_recent: int = 14,
        budget_tokens: int = 60000,
        strategy: str = "smart",
        compact_trigger: Optional[int] = None,  # 超过该 token 预算才触发 (None=用 budget)
        summarize: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
        importance_scorer: Optional[Callable[[Dict[str, Any]], float]] = None,
        dedupe: bool = False,  # 压缩前自动折叠连续重复消息 (默认关: 保持历史行为)
    ) -> None:
        self.keep_recent = keep_recent
        self.budget_tokens = budget_tokens
        # 触发阈值: 默认等于预算, 可配置为略低于预算以提前压缩, 避免临界点抖动
        self.compact_trigger = compact_trigger or budget_tokens
        self.strategy = strategy
        self._summarize = summarize  # (消息列表) -> summary_text
        self._importance = importance_scorer or message_importance
        self._dedupe = dedupe
        self._stats: Dict[str, Any] = {
            "compactions": 0, "dropped": 0, "deduped": 0,
            "last": {}, "history": [],
        }

    # ------------------------------------------------------------ 统计

    def stats(self) -> Dict[str, Any]:
        """压缩统计快照: 次数 / 累计折叠 / 累计去重 / 最近质量指标 / 历史。"""
        return {
            "compactions": self._stats["compactions"],
            "dropped": self._stats["dropped"],
            "deduped": self._stats["deduped"],
            "last": dict(self._stats["last"]),
            "history": list(self._stats["history"]),
        }

    def _record(self, before: int, dropped: int, metrics: Optional[Dict[str, Any]]) -> None:
        s = self._stats
        s["compactions"] += 1
        s["dropped"] += dropped
        if metrics:
            s["last"] = metrics
            s["history"].append(metrics)
            s["history"] = s["history"][-20:]  # 只留最近 20 次, 防无限增长

    def drop_consecutive_duplicates(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """折叠连续重复消息 (同 role+content, tool 结果除外), 返回去重后的列表。"""
        if not messages:
            return messages
        out: List[Dict[str, Any]] = [messages[0]]
        for m in messages[1:]:
            if not _same_content(out[-1], m):
                out.append(m)
        self._stats["deduped"] += len(messages) - len(out)
        return out

    # ------------------------------------------------------------ 压缩

    def compact_if_needed(self, messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """返回 (压缩后的消息列表, 被折叠的消息条数)。无需压缩则返回原样 (dropped=0)。

        对标 Claude Code 的 compact: 系统提示前缀永远不动 (缓存友好), 只把中间段
        用模型摘要折叠成一条; 若仍超预算, 迭代压缩直到回到预算内 (上限 5 次防死循环)。
        融合 (fusion) 策略单次即已构造 head/elision/tail/summary 完整形状、文本身已逼近
        预算, 再次折叠只会把摘要与 elision 标记当新正文反复压缩 (丢最近消息、条数回弹),
        因此融合路径只跑一趟即止。
        """
        total_dropped = 0
        current = messages

        # 可选: 压缩前先折叠连续重复消息 (opt-in, 默认关闭保持历史行为)
        if self._dedupe and estimate_messages(current) > self.compact_trigger:
            deduped = self.drop_consecutive_duplicates(current)
            if len(deduped) < len(current):
                current = deduped

        for _ in range(5):
            if estimate_messages(current) <= self.compact_trigger:
                break
            before = estimate_messages(current)
            compacted, dropped = self._compact_once(current)
            total_dropped += dropped
            metrics = compaction_metrics(current, compacted, self._importance)
            self._record(before, dropped, metrics)
            if compacted is current:  # 已无法继续压缩
                break
            current = compacted
            if self._summarize is not None:
                # 融合策略单趟成形: 不再重复折叠 (见 docstring 说明)
                break
        return current, total_dropped

    def needs_compact(self, messages: List[Dict[str, Any]]) -> bool:
        """预判是否即将触发自动压缩 (供 PreCompact hook 与调用方提前感知)。"""
        return estimate_messages(messages) > self.compact_trigger

    def compact_force(self, messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """强制压缩 (对标 Claude Code /compact): 忽略预算阈值, 折叠中间历史直到无法再压缩。"""
        total_dropped = 0
        current = messages
        for _ in range(5):
            before = estimate_messages(current)
            compacted, dropped = self._compact_once_force(current)
            total_dropped += dropped
            metrics = compaction_metrics(current, compacted, self._importance)
            self._record(before, dropped, metrics)
            if compacted is current:  # 已无法继续压缩
                break
            current = compacted
        return current, total_dropped

    def _compact_once_force(self, messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """强制单次压缩: 忽略预算阈值, 但**永远保留最近 keep_recent 条**。

        此前实现把 max_tokens=0 直接喂给 elision, 导致 head/tail 预算双双为 0,
        连最新一条用户消息都被删除 → 违反「最近上下文最宝贵」的不变量。改为走
        原生「保留最近 + 折叠中间」形状, 保证手动 /compact 不丢最近上下文。
        """
        system = messages[:1] if messages and messages[0].get("role") == "system" else []
        body = messages[len(system):]
        if len(body) <= 1:
            return messages, 0
        return self._compact_once_native(system, body)

    def _compact_once(self, messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """单次压缩 (不动 system 前缀)。

        优先使用 head/tail/elision 压缩形状 (同时保留最旧与最新上下文, 只省略中间);
        无摘要能力时退回原生「保留最近 + 折叠中间」策略。
        """
        # 系统提示必须唯一且置首
        system = messages[:1] if messages and messages[0].get("role") == "system" else []
        body = messages[len(system):]

        if len(body) <= self.keep_recent + 1:
            # 已经很短, 但仍超预算 (说明单条巨大): 不做结构性折叠, 留给模型层
            return messages, 0

        # head/tail/elision 压缩形状 (保留首尾, 省略中间)
        if self._summarize is not None:
            return self._compact_once_fusion(system, body)

        return self._compact_once_native(system, body)

    def _compact_once_native(
        self, system: List[Dict[str, Any]], body: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], int]:
        """原生压缩: 保留最近 keep_recent, 折叠中间为一条摘要 (或占位提示)。"""
        boundary = max(0, len(body) - self.keep_recent)
        # tool 消息必须跟在包含对应 tool_calls 的 assistant 消息之后，不能从
        # 一组调用中间截断。向前移动边界会多保留少量消息，但保证协议合法。
        while boundary > 0 and body[boundary].get("role") == "tool":
            boundary -= 1
        recent = body[boundary:]
        middle = body[:boundary]
        dropped = len(middle)

        if self.strategy == "smart" and self._summarize and middle:
            # 直接传原始消息列表, 由 summarize 回调自行序列化,
            # 避免传入字符串导致迭代器按字符遍历的 bug。
            try:
                summary = self._summarize(middle)
            except Exception:
                summary = ""
            if summary:
                summary_msg: Dict[str, Any] = {
                    "role": "user",
                    "content": f"[早期上下文摘要 · 已折叠 {dropped} 条历史]\n{summary}",
                }
                return system + [summary_msg] + recent, dropped

        # none 策略 / 摘要失败: 直接丢弃中间, 留一个占位提示
        placeholder: Dict[str, Any] = {
            "role": "user",
            "content": f"[上下文压缩] 为节省空间, 已折叠中间的 {dropped} 条历史。"
                       "如需回顾, 可用 memory_search 检索长期记忆或 read_file 看文件现状。",
        }
        return system + [placeholder] + recent, dropped

    def _compact_once_fusion(
        self, system: List[Dict[str, Any]], body: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], int]:
        """head/tail/elision 压缩形状 (保留首尾, 省略中间)。

        无摘要能力时优雅退回原生策略, 保证不丢上下文。
        """
        try:
            summary = self._summarize(body)
        except Exception:
            summary = ""
        if not summary:
            return self._compact_once_native(system, body)
        compacted = compact_head_tail_elision(
            system + body, summary,
            max_tokens=self.compact_trigger, head_tokens=2_000,
            importance_scorer=self._importance,
        )
        # 融合形状依赖 head/tail/elision/summary 进/出消息条数: 对短正文, 标记位
        # 可能抵消掉 head/tail 省下的条数, 导致总条数不降反增。此时退回原生策略
        # (保留最近 keep_recent + 折叠中间为一条), 恒保证「条数下降 + 最近上下文」。
        if len(compacted) >= len(system + body):
            return self._compact_once_native(system, body)
        kept_body = [
            m for m in compacted
            if m.get("role") != "system"
            and not (isinstance(m.get("content"), str) and m["content"].startswith("[compaction]"))
            and not (isinstance(m.get("content"), str) and m["content"].startswith("[早期上下文摘要]"))
        ]
        dropped = max(0, len(body) - len(kept_body))
        return compacted, dropped

    @staticmethod
    def _serialize(messages: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for m in messages:
            role = m.get("role", "?")
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(str(p) for p in content)
            if m.get("tool_calls"):
                calls = "; ".join(
                    f"{tc.get('function', {}).get('name')}({tc.get('function', {}).get('arguments', '')})"
                    for tc in m["tool_calls"]
                )
                content = f"[调用工具] {calls}"
            elif role == "tool":
                content = f"[工具结果] {content[:1500]}"
            parts.append(f"[{role}] {content[:2000]}")
        return "\n\n".join(parts)
