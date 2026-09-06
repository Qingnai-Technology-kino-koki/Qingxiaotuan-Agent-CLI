"""kernel session 契约层 —— 会话上下文的数据结构。

自研实现：
- ContextMessage = Message & { id?, origin?, note?, isError? }
- PromptOrigin.kind ∈ user | skill_activation | injection | compaction_summary |
  shell_command | system_trigger | task | cron_job | hook_result | retry
- LoopRecordedEvent（tool.result / content.part 等循环事件）

复用 qingxiaotuan.kernel.contract 的 Message / Role / ContentPart / ToolCall / text_part。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from ..contract import ContentPart, Message, Role, ToolCall, text_part


# ============================================================ Origin 来源种类

class OriginKind(str, enum.Enum):
    USER = "user"
    SKILL_ACTIVATION = "skill_activation"
    PLUGIN_COMMAND = "plugin_command"
    INJECTION = "injection"
    SHELL_COMMAND = "shell_command"
    COMPACTION_SUMMARY = "compaction_summary"
    SYSTEM_TRIGGER = "system_trigger"
    TASK = "task"
    CRON_JOB = "cron_job"
    CRON_MISSED = "cron_missed"
    HOOK_RESULT = "hook_result"
    RETRY = "retry"

    def __str__(self) -> str:
        return self.value


# ============================================================ Origin（消息来源）

@dataclass
class Origin:
    """一条消息的来源标记。"""

    kind: OriginKind
    variant: Optional[str] = None
    trigger: Optional[str] = None
    owner_prompt_id: Optional[str] = None
    # 其余字段（skillName / pluginId / taskId / jobId / event ...）统一进 extra
    extra: Dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------- 便捷构造
    @classmethod
    def user(cls) -> "Origin":
        return cls(kind=OriginKind.USER)

    @classmethod
    def compaction_summary(cls) -> "Origin":
        return cls(kind=OriginKind.COMPACTION_SUMMARY)

    @classmethod
    def injection(cls, variant: Optional[str] = None) -> "Origin":
        return cls(kind=OriginKind.INJECTION, variant=variant)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["Origin"]:
        if not isinstance(data, dict):
            return None
        kind_raw = data.get("kind")
        try:
            kind = OriginKind(kind_raw) if kind_raw is not None else OriginKind.USER
        except ValueError:
            kind = OriginKind.USER
        known = {"kind", "variant", "trigger", "owner_prompt_id"}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            kind=kind,
            variant=data.get("variant"),
            trigger=data.get("trigger"),
            owner_prompt_id=data.get("owner_prompt_id"),
            extra=extra,
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": str(self.kind)}
        if self.variant is not None:
            out["variant"] = self.variant
        if self.trigger is not None:
            out["trigger"] = self.trigger
        if self.owner_prompt_id is not None:
            out["owner_prompt_id"] = self.owner_prompt_id
        if self.extra:
            out.update(self.extra)
        return out


# ============================================================ ContextMessage

@dataclass
class ContextMessage(Message):
    """对话消息（继承 kernel Message），附加 id / origin / note / is_error。"""

    id: Optional[str] = None
    origin: Optional[Origin] = None
    note: Optional[str] = None
    is_error: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        out = super().to_dict()
        if self.id is not None:
            out["id"] = self.id
        if self.origin is not None:
            out["origin"] = self.origin.to_dict()
        if self.note is not None:
            out["note"] = self.note
        if self.is_error is not None:
            out["is_error"] = self.is_error
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextMessage":
        role = Role(data["role"]) if isinstance(data.get("role"), str) else Role.USER
        raw_content = data.get("content", "")
        if isinstance(raw_content, str):
            content: Union[str, List[ContentPart]] = raw_content
        elif isinstance(raw_content, list):
            content = [
                ContentPart(
                    type=p.get("type", "text"),
                    text=p.get("text"),
                    image_url=p.get("image_url"),
                    input_audio=p.get("input_audio"),
                )
                for p in raw_content
                if isinstance(p, dict)
            ]
        else:
            content = ""
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("name", "") or tc.get("function", {}).get("name", ""),
                arguments=tc.get("arguments", "")
                or tc.get("function", {}).get("arguments", ""),
            )
            for tc in (data.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        return cls(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            id=data.get("id"),
            origin=Origin.from_dict(data.get("origin")),
            note=data.get("note"),
            is_error=data.get("is_error"),
        )


# ============================================================ LoopRecordedEvent

@dataclass
class LoopToolResult:
    """tool.result 事件的结果体。"""

    output: Union[str, List[ContentPart]] = ""
    is_error: Optional[bool] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"output": self.output}
        if self.is_error is not None:
            out["is_error"] = self.is_error
        if self.note is not None:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoopToolResult":
        out = data.get("output", "")
        if isinstance(out, list):
            out_val: Union[str, List[ContentPart]] = [
                ContentPart(
                    type=p.get("type", "text"),
                    text=p.get("text"),
                    image_url=p.get("image_url"),
                    input_audio=p.get("input_audio"),
                )
                for p in out
                if isinstance(p, dict)
            ]
        else:
            out_val = out if isinstance(out, str) else str(out)
        return cls(
            output=out_val,
            is_error=data.get("is_error"),
            note=data.get("note"),
        )


@dataclass
class LoopRecordedEvent:
    """循环记录事件）。

    本模块只深度使用 tool.result（落盘为 tool 消息）；其余类型（step.begin /
    content.part / tool.call / step.end）保留字段以便 wire 持久化与未来扩展。
    """

    type: str  # 'step.begin' | 'step.end' | 'content.part' | 'tool.call' | 'tool.result'
    uuid: Optional[str] = None
    step_uuid: Optional[str] = None
    step: Optional[int] = None
    turn_id: Optional[str] = None
    part: Optional[ContentPart] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    args: Optional[Any] = None
    parent_uuid: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Optional[Any] = None
    result: Optional[LoopToolResult] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type}
        for key in ("uuid", "step_uuid", "step", "turn_id", "tool_call_id",
                    "name", "args", "parent_uuid", "finish_reason"):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        if self.part is not None:
            out["part"] = self.part.to_dict()
        if self.usage is not None:
            out["usage"] = self.usage
        if self.result is not None:
            out["result"] = self.result.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoopRecordedEvent":
        raw_part = data.get("part")
        part = (
            ContentPart(
                type=raw_part.get("type", "text"),
                text=raw_part.get("text"),
                image_url=raw_part.get("image_url"),
                input_audio=raw_part.get("input_audio"),
            )
            if isinstance(raw_part, dict)
            else None
        )
        raw_result = data.get("result")
        result = LoopToolResult.from_dict(raw_result) if isinstance(raw_result, dict) else None
        return cls(
            type=data.get("type", ""),
            uuid=data.get("uuid"),
            step_uuid=data.get("step_uuid"),
            step=data.get("step"),
            turn_id=data.get("turn_id"),
            part=part,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            args=data.get("args"),
            parent_uuid=data.get("parent_uuid"),
            finish_reason=data.get("finish_reason"),
            usage=data.get("usage"),
            result=result,
        )


# ============================================================ Compaction 输入/结果

@dataclass
class CompactionInput:
    """apply_compaction 的输入）。"""

    summary: str
    context_summary: Optional[str] = None
    compacted_count: int = 0
    tokens_before: int = 0
    tokens_after: Optional[int] = None
    summary_output_tokens: Optional[int] = None
    request_overhead_tokens: Optional[int] = None
    kept_user_message_count: Optional[int] = None
    dropped_count: Optional[int] = None


@dataclass
class CompactionResult:
    """apply_compaction 的输出）。"""

    summary: str
    compacted_count: int
    tokens_before: int
    tokens_after: int
    kept_user_message_count: int
    dropped_count: int
    messages: List[ContextMessage]
