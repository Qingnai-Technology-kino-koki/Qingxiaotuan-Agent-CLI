"""压缩子系统 —— 上下文超限时的摘要收拢。

提供：
- estimate_tokens / estimate_tokens_for_message / estimate_tokens_for_messages（启发式）
- select_compaction_user_messages（head / tail / elision 四段式）
- build_compaction_shape（构造压缩后的消息列表）
- FullCompaction（should_compact / should_block / run —— run 用注入的 generate_fn 生成摘要）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Union

from ..contract import ContentPart, Role, create_user_message, text_part
from .contracts import ContextMessage, Origin, OriginKind
from .strategy import CompactionConfig, DEFAULT_COMPACTION_CONFIG


# ============================================================ 常量

COMPACT_MAX_TOKENS = 20_000
COMPACT_HEAD_TOKENS = 2_000
COMPACTION_ELISION_VARIANT = "compaction_elision"
MEDIA_TOKEN_ESTIMATE = 2_000

COMPACTION_INSTRUCTION = (
    "Summarize the earlier part of this conversation concisely. "
    "Preserve decisions, user intent, file paths, and any open tasks. "
    "Output only the summary text."
)


# ============================================================ token 估算

def estimate_tokens(text: str) -> int:
    """ASCII 每 4 字符≈1 token，非 ASCII 每字符≈1 token。"""
    if not text:
        return 0
    ascii_count = 0
    non_ascii_count = 0
    for char in text:
        if ord(char) <= 127:
            ascii_count += 1
        else:
            non_ascii_count += 1
    return math.ceil(ascii_count / 4) + non_ascii_count


def estimate_tokens_for_content_part(part: ContentPart) -> int:
    if part.type == "text":
        return estimate_tokens(part.text or "")
    if part.type in ("image_url", "audio_url", "video_url"):
        return MEDIA_TOKEN_ESTIMATE
    return 0


def estimate_tokens_for_message(message: ContextMessage) -> int:
    total = estimate_tokens(str(message.role))
    content = message.content
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, ContentPart):
                total += estimate_tokens_for_content_part(part)
            else:
                total += estimate_tokens(str(part))
    for call in message.tool_calls:
        total += estimate_tokens(call.name)
        total += estimate_tokens(call.arguments if isinstance(call.arguments, str) else str(call.arguments))
    return total


def estimate_tokens_for_messages(messages: List[ContextMessage]) -> int:
    return sum(estimate_tokens_for_message(m) for m in messages)


# ============================================================ 可压缩用户消息过滤

def is_compaction_summary_message(message: ContextMessage) -> bool:
    return message.origin is not None and message.origin.kind == OriginKind.COMPACTION_SUMMARY


def compaction_user_message_disposition(origin: Optional[Origin]) -> str:
    if origin is None:
        return "keep"
    if origin.kind == OriginKind.USER:
        return "keep"
    if origin.kind in (OriginKind.SKILL_ACTIVATION, OriginKind.PLUGIN_COMMAND):
        return "keep" if origin.trigger == "user-slash" else "drop"
    return "drop"


def is_real_user_input(message: ContextMessage) -> bool:
    return message.role == Role.USER and compaction_user_message_disposition(message.origin) == "keep"


def collect_compactable_user_messages(history: List[ContextMessage]) -> List[ContextMessage]:
    return [m for m in history if is_real_user_input(m) and not is_compaction_summary_message(m)]


# ============================================================ 文本截断辅助

def _extract_text(content: Union[str, List[ContentPart]]) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.text or "" for p in content if isinstance(p, ContentPart) and p.type == "text")


def _truncate_text_to_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    ascii_count = 0
    non_ascii_count = 0
    end = 0
    for char in text:
        if ord(char) <= 127:
            ascii_count += 1
        else:
            non_ascii_count += 1
        if math.ceil(ascii_count / 4) + non_ascii_count > max_tokens:
            break
        end += 1
    return text[:end]


def _truncate_text_to_tokens_from_end(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    ascii_count = 0
    non_ascii_count = 0
    start = len(text)
    for i in range(len(text) - 1, -1, -1):
        code = ord(text[i])
        is_ascii = code <= 127
        if is_ascii:
            ascii_count += 1
        else:
            non_ascii_count += 1
        if math.ceil(ascii_count / 4) + non_ascii_count > max_tokens:
            break
        start = i
    return text[start:]


def _replace_message_text(message: ContextMessage, text: str) -> ContextMessage:
    return ContextMessage(
        role=message.role,
        content=[text_part(text)],
        tool_calls=[],
        tool_call_id=message.tool_call_id,
        name=message.name,
        id=message.id,
        origin=message.origin,
        note=message.note,
        is_error=message.is_error,
    )


def _truncate_user_message(message: ContextMessage, max_tokens: int) -> ContextMessage:
    return _replace_message_text(message, _truncate_text_to_tokens(_extract_text(message.content), max_tokens))


# ============================================================ 选择压缩用户消息

@dataclass
class CompactionUserSelection:
    head: List[ContextMessage]
    tail: List[ContextMessage]
    elided: bool
    omitted_tokens: int


def select_compaction_user_messages(
    messages: List[ContextMessage],
    max_tokens: int = COMPACT_MAX_TOKENS,
    head_tokens: int = COMPACT_HEAD_TOKENS,
    estimate: Callable[[ContextMessage], int] = estimate_tokens_for_message,
) -> CompactionUserSelection:
    """head 保留最近 head_tokens，tail 保留最近 (max_tokens - head_tokens)，中间用 elision 省略。"""
    total_tokens = sum(estimate(m) for m in messages)
    if total_tokens <= max_tokens:
        return CompactionUserSelection(head=[], tail=list(messages), elided=False, omitted_tokens=0)

    head_budget = min(max(head_tokens, 0), max_tokens)
    tail_budget = max_tokens - head_budget

    tail: List[ContextMessage] = []
    tail_remaining = tail_budget
    head_end_exclusive = len(messages)
    tail_boundary_dropped_prefix: Optional[ContextMessage] = None

    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        tokens = estimate(message)
        if tokens <= tail_remaining:
            tail.append(message)
            tail_remaining -= tokens
            head_end_exclusive = i
            continue
        full_text = _extract_text(message.content)
        kept_suffix = _truncate_text_to_tokens_from_end(full_text, tail_remaining)
        tail.append(_replace_message_text(message, kept_suffix))
        head_end_exclusive = i
        dropped_prefix = full_text[: len(full_text) - len(kept_suffix)]
        if dropped_prefix:
            tail_boundary_dropped_prefix = _replace_message_text(message, dropped_prefix)
        break
    tail.reverse()

    head_candidates = list(messages[:head_end_exclusive])
    if tail_boundary_dropped_prefix is not None:
        head_candidates.append(tail_boundary_dropped_prefix)

    head: List[ContextMessage] = []
    head_remaining = head_budget
    for message in head_candidates:
        if head_remaining <= 0:
            break
        tokens = estimate(message)
        if tokens <= head_remaining:
            head.append(message)
            head_remaining -= tokens
            continue
        head.append(_truncate_user_message(message, head_remaining))
        break

    kept_tokens = sum(estimate(m) for m in head) + sum(estimate(m) for m in tail)
    return CompactionUserSelection(
        head=head,
        tail=tail,
        elided=True,
        omitted_tokens=max(0, total_tokens - kept_tokens),
    )


# ============================================================ 构造压缩后的消息

def build_compaction_elision_text(omitted_tokens: int) -> str:
    return (
        f"[compaction] Some user messages were omitted here: the messages above are the "
        f"oldest user input, the messages below are the most recent, and roughly "
        f"{omitted_tokens} tokens in between were dropped. The omitted content is covered "
        f"by the compaction summary at the end of the conversation."
    )


def create_compaction_elision_message(omitted_tokens: int) -> ContextMessage:
    return ContextMessage(
        role=Role.USER,
        content=[text_part(build_compaction_elision_text(omitted_tokens))],
        tool_calls=[],
        origin=Origin.injection(COMPACTION_ELISION_VARIANT),
    )


def create_compaction_summary_message(text: str) -> ContextMessage:
    return ContextMessage(
        role=Role.USER,
        content=[text_part(text)],
        tool_calls=[],
        origin=Origin.compaction_summary(),
    )


def build_compaction_shape(
    history: List[ContextMessage],
    summary_text: str,
    *,
    head_tokens: int = COMPACT_HEAD_TOKENS,
    tail_tokens: int = COMPACT_MAX_TOKENS,
    estimate: Callable[[ContextMessage], int] = estimate_tokens_for_message,
) -> List[ContextMessage]:
    """构造压缩后的消息列表：[head..., elision?, tail..., summary]。"""
    compactable = collect_compactable_user_messages(history)
    selection = select_compaction_user_messages(compactable, tail_tokens, head_tokens, estimate)
    kept: List[ContextMessage] = list(selection.head)
    if selection.elided:
        kept.append(create_compaction_elision_message(selection.omitted_tokens))
    kept.extend(selection.tail)
    kept.append(create_compaction_summary_message(summary_text))
    return kept


# ============================================================ 溢出收缩

COMPACTION_OVERFLOW_SHRINK_RATIOS = (0.7, 0.5, 0.35)
MAX_OVERFLOW_SHRINK_ATTEMPTS = 3


def _take_recent_messages_within_budget(
    messages: List[ContextMessage],
    token_budget: int,
    estimate: Callable[[ContextMessage], int],
) -> List[ContextMessage]:
    start = len(messages)
    tokens = 0
    for i in range(len(messages) - 1, -1, -1):
        message_tokens = estimate(messages[i])
        if tokens + message_tokens > token_budget:
            break
        tokens += message_tokens
        start = i
    if start == 0:
        start = 1
    # 丢弃前导 tool 消息（必须跟随 assistant）
    while start < len(messages) and messages[start].role == Role.TOOL:
        start += 1
    return messages[start:]


def shrink_compaction_history_after_overflow(
    messages: List[ContextMessage],
    attempt: int,
    estimate: Callable[[ContextMessage], int] = estimate_tokens_for_message,
) -> List[ContextMessage]:
    if len(messages) <= 1:
        return list(messages)
    ratio = COMPACTION_OVERFLOW_SHRINK_RATIOS[min(attempt - 1, len(COMPACTION_OVERFLOW_SHRINK_RATIOS) - 1)]
    total_tokens = sum(estimate(m) for m in messages)
    token_budget = int(total_tokens * ratio)
    return _take_recent_messages_within_budget(messages, token_budget, estimate)


def strip_dynamic_tool_context(messages: List[ContextMessage]) -> List[ContextMessage]:
    """剥离动态工具上下文（无该特性时原样返回）。"""
    return list(messages)


# ============================================================ FullCompaction

class FullCompaction:
    """全量压缩控制器：调度策略与执行。

    - should_compact / should_block 用 DEFAULT_COMPACTION_CONFIG 判定是否该压缩。
    - run() 调用注入的 generate_fn(system_prompt, messages) -> str 生成摘要，
      随后返回 build_compaction_shape 构造的压缩消息；遇到生成异常按 0.7/0.5/0.35
      比例收缩历史后重试（溢出收缩）。
    """

    def __init__(self, config: Optional[CompactionConfig] = None) -> None:
        self.config = config or DEFAULT_COMPACTION_CONFIG

    # ---------------------------------------------------------- 触发判定
    def _should_use_reserved(self, used: int, max_size: int) -> bool:
        reserved = self.config.reserved_context_size
        return reserved > 0 and reserved < max_size and used + reserved >= max_size

    def should_compact(self, used: int, max_size: int) -> bool:
        if max_size <= 0:
            return False
        return used >= max_size * self.config.trigger_ratio or self._should_use_reserved(used, max_size)

    def should_block(self, used: int, max_size: int) -> bool:
        if max_size <= 0:
            return False
        return used >= max_size * self.config.block_ratio or self._should_use_reserved(used, max_size)

    # ---------------------------------------------------------- 执行一次压缩
    async def run(
        self,
        history: List[ContextMessage],
        generate_fn: Callable[[str, List[ContextMessage]], Any],
        *,
        source: str = "auto",
    ) -> List[ContextMessage]:
        """生成摘要并返回压缩后的消息列表。generate_fn 为注入的摘要生成函数。"""
        model_messages = strip_dynamic_tool_context(history) + [create_user_message(COMPACTION_INSTRUCTION)]

        overflow_shrink = 0
        summary: Optional[str] = None
        while True:
            try:
                result = await generate_fn("", model_messages)
                summary = result if isinstance(result, str) else str(result)
                if not summary or not summary.strip():
                    raise ValueError("compaction summary is empty")
                break
            except Exception:
                if overflow_shrink < MAX_OVERFLOW_SHRINK_ATTEMPTS and len(model_messages) > 1:
                    overflow_shrink += 1
                    model_messages = shrink_compaction_history_after_overflow(
                        model_messages, overflow_shrink
                    )
                    continue
                raise

        return build_compaction_shape(history, summary or "")
