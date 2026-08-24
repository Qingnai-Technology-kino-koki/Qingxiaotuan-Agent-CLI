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
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

_SUMMARY_PROMPT = (
    "你是一个上下文压缩器。下面是一段较早的对话历史 (用户指令、你的思考、工具调用与结果)。"
    "请压缩成一份**结构化中文摘要**, 必须保住: 用户的长期目标与约束、已做出的关键决策、"
    "已修改/创建的文件及其目的、失败的尝试与原因、待办项、以及任何用户明确表达的偏好。"
    "丢弃琐碎的步骤细节与重复内容。用要点列出, 不超过 400 字。只输出摘要本身。"
)


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


class ContextManager:
    """在 Agent 主循环里被调用, 负责按需压缩历史。"""

    def __init__(
        self,
        keep_recent: int = 14,
        budget_tokens: int = 60000,
        strategy: str = "smart",
        compact_trigger: Optional[int] = None,  # 超过该 token 预算才触发 (None=用 budget)
        summarize: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.keep_recent = keep_recent
        self.budget_tokens = budget_tokens
        # 触发阈值: 默认等于预算, 可配置为略低于预算以提前压缩, 避免临界点抖动
        self.compact_trigger = compact_trigger or budget_tokens
        self.strategy = strategy
        self._summarize = summarize  # (text) -> summary_text

    def compact_if_needed(self, messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """返回 (压缩后的消息列表, 被折叠的消息条数)。无需压缩则返回原样 (dropped=0)。

        对标 Claude Code 的 compact: 系统提示前缀永远不动 (缓存友好), 只把中间段
        用模型摘要折叠成一条; 若仍超预算, 迭代压缩直到回到预算内 (上限 5 次防死循环)。
        """
        total_dropped = 0
        current = messages
        for _ in range(5):
            if estimate_messages(current) <= self.compact_trigger:
                break
            compacted, dropped = self._compact_once(current)
            total_dropped += dropped
            if compacted is current:  # 已无法继续压缩
                break
            current = compacted
        return current, total_dropped

    def _compact_once(self, messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """单次压缩 (不动 system 前缀)。"""
        # 系统提示必须唯一且置首
        system = messages[:1] if messages and messages[0].get("role") == "system" else []
        body = messages[len(system):]

        if len(body) <= self.keep_recent + 1:
            # 已经很短, 但仍超预算 (说明单条巨大): 不做结构性折叠, 留给模型层
            return messages, 0

        boundary = max(0, len(body) - self.keep_recent)
        # tool 消息必须跟在包含对应 tool_calls 的 assistant 消息之后，不能从
        # 一组调用中间截断。向前移动边界会多保留少量消息，但保证协议合法。
        while boundary > 0 and body[boundary].get("role") == "tool":
            boundary -= 1
        recent = body[boundary:]
        middle = body[:boundary]
        dropped = len(middle)

        if self.strategy == "smart" and self._summarize and middle:
            middle_text = self._serialize(middle)
            try:
                summary = self._summarize(middle_text)
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
