"""ContextMemory —— 内存版会话上下文，负责积累与折叠消息。

- get / append / append_loop_event / undo / apply_compaction / clear
- compute_undo_cut：遇 compaction_summary / injection 停止，按 undo anchor（真实用户输入）计数
- build_messages(system_prompt, history) 便捷函数
"""

from __future__ import annotations

from typing import List, Tuple

from ..contract import Role, create_system_message
from .contracts import (
    CompactionInput,
    CompactionResult,
    ContextMessage,
    LoopRecordedEvent,
    Origin,
    OriginKind,
)
from .compaction import (
    build_compaction_shape,
    estimate_tokens_for_messages,
    is_real_user_input,
)


# ============================================================ undo 锚点判定

def is_undo_anchor(message: ContextMessage) -> bool:
    """只有「真实用户输入」才算一个 undo 单元。"""
    if message.role != Role.USER:
        return False
    origin = message.origin
    if origin is None or origin.kind == OriginKind.USER:
        return True
    if origin.kind in (OriginKind.SKILL_ACTIVATION, OriginKind.PLUGIN_COMMAND):
        return origin.trigger == "user-slash"
    return False


def is_prompt_owned_injection(message: ContextMessage, prompt: ContextMessage) -> bool:
    origin = message.origin
    return (
        origin is not None
        and origin.kind == OriginKind.INJECTION
        and origin.owner_prompt_id is not None
        and prompt.id is not None
        and origin.owner_prompt_id == prompt.id
    )


def compute_undo_cut(
    state: List[ContextMessage], count: int
) -> Tuple[int, int, bool]:
    """从末尾向前数 `count` 个 undo anchor，返回 (cut_index, removed_count, stopped_at_compaction)。

    - 遇到 injection 来源的消息直接跳过（不计入、不截断）。
    - 遇到 compaction_summary 停止（不能跨越压缩边界）。
    - cut_index 为最后一个被移除 anchor 的下标；其前的 prompt-owned injection 一起并入截断区。
    """
    remaining = count
    cut_index = -1
    removed_count = 0
    stopped_at_compaction = False
    i = len(state) - 1
    while i >= 0 and remaining > 0:
        message = state[i]
        if message is None:
            continue
        if message.origin is not None and message.origin.kind == OriginKind.INJECTION:
            continue
        if message.origin is not None and message.origin.kind == OriginKind.COMPACTION_SUMMARY:
            stopped_at_compaction = True
            break
        if is_undo_anchor(message):
            remaining -= 1
            removed_count += 1
            cut_index = i
            while cut_index > 0 and is_prompt_owned_injection(state[cut_index - 1], message):
                cut_index -= 1
        i -= 1
    return cut_index, removed_count, stopped_at_compaction


# ============================================================ ContextMemory

class ContextMemory:
    """内存上下文存储器。"""

    def __init__(self, messages: List[ContextMessage] | None = None) -> None:
        self._messages: List[ContextMessage] = list(messages) if messages else []

    # ---------------------------------------------------------- 读取
    def get(self) -> List[ContextMessage]:
        return list(self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    # ---------------------------------------------------------- 追加
    def append(self, *messages: ContextMessage) -> None:
        for message in messages:
            self._messages.append(message)

    def append_loop_event(self, event: LoopRecordedEvent) -> None:
        """把一条循环事件 fold 进上下文。"""
        if event.type == "tool.result":
            result = event.result
            output = result.output if result is not None else ""
            self._messages.append(
                ContextMessage(
                    role=Role.TOOL,
                    content=output,
                    tool_call_id=event.tool_call_id,
                    is_error=result.is_error if result is not None else None,
                    note=result.note if result is not None else None,
                )
            )
            return

        if event.type == "content.part":
            part = event.part
            if part is None:
                return
            last = self._messages[-1] if self._messages else None
            if last is not None and last.role == Role.ASSISTANT and not last.tool_calls:
                if isinstance(last.content, list):
                    last.content = list(last.content) + [part]
                else:
                    last.content = [part]
                return
            self._messages.append(
                ContextMessage(role=Role.ASSISTANT, content=[part] if part is not None else "")
            )
            return

        # step.begin / step.end / tool.call 等：本简化实现仅记录，不改变结构
        return

    # ---------------------------------------------------------- undo
    def undo(self, count: int) -> Tuple[int, int, bool]:
        """撤销最近 `count` 个用户输入回合。返回 compute_undo_cut 的结果。"""
        if count <= 0:
            return -1, 0, False
        cut_index, removed_count, stopped = compute_undo_cut(self._messages, count)
        if cut_index >= 0 and removed_count >= count:
            self._messages = self._messages[:cut_index]
        return cut_index, removed_count, stopped

    # ---------------------------------------------------------- 压缩
    def apply_compaction(self, input: "CompactionInput | str | dict") -> CompactionResult:
        """用一条摘要替换历史（压缩）。"""
        if isinstance(input, str):
            summary = input
            compacted_count = len(self._messages)
        elif isinstance(input, CompactionInput):
            summary = input.context_summary or input.summary
            compacted_count = input.compacted_count or len(self._messages)
        elif isinstance(input, dict):
            summary = input.get("contextSummary") or input.get("summary") or ""
            compacted_count = int(input.get("compactedCount") or len(self._messages))
        else:
            raise TypeError(f"unsupported compaction input: {type(input)!r}")

        history = list(self._messages)
        messages = build_compaction_shape(history, summary)
        tokens_before = estimate_tokens_for_messages(history)
        tokens_after = estimate_tokens_for_messages(messages)
        kept = sum(1 for m in messages if is_real_user_input(m))
        orig_kept = sum(1 for m in history if is_real_user_input(m))
        dropped = max(0, orig_kept - kept)
        self._messages = list(messages)
        return CompactionResult(
            summary=summary,
            compacted_count=compacted_count,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            kept_user_message_count=kept,
            dropped_count=dropped,
            messages=list(messages),
        )

    # ---------------------------------------------------------- 清空
    def clear(self) -> None:
        self._messages = []


# ============================================================ 便捷函数

def build_messages(
    system_prompt: str, history: List[ContextMessage]
) -> List[ContextMessage]:
    """组装发送给模型的消息列表：[system] + history。"""
    return [create_system_message(system_prompt), *history]
