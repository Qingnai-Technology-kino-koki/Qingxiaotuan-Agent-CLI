"""Transcript message model types and (de)serialization (对齐上游 model/* 的类型/序列化).

All model entities are immutable dataclasses that round-trip to/from plain dicts
(keyed in camelCase, matching the TypeScript wire format). Free-form payloads
(origin, payload, request/response, tool input/output, etc.) are preserved as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .exceptions import TranscriptDecodeError
from .ids import (
    AttachmentId,
    FrameId,
    InteractionId,
    MarkerId,
    PromptId,
    StepId,
    TaskId,
    TaskRefId,
    TodoId,
    TurnId,
)

# --------------------------------------------------------------------------- #
# attachment.ts
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptAttachment:
    attachment_id: AttachmentId
    media_type: str
    name: Optional[str] = None
    size: Optional[int] = None
    source: Optional[dict] = None  # AttachmentSource union (url|file|session_media)
    placeholder: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {"attachmentId": self.attachment_id, "mediaType": self.media_type}
        if self.name is not None:
            d["name"] = self.name
        if self.size is not None:
            d["size"] = self.size
        if self.source is not None:
            d["source"] = self.source
        if self.placeholder is not None:
            d["placeholder"] = self.placeholder
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptAttachment":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("attachment must be an object")
        aid = d.get("attachmentId")
        mt = d.get("mediaType")
        if not isinstance(aid, str) or not isinstance(mt, str):
            raise TranscriptDecodeError("attachment requires string attachmentId and mediaType")
        return cls(
            attachment_id=aid,
            media_type=mt,
            name=d.get("name"),
            size=d.get("size"),
            source=d.get("source"),
            placeholder=d.get("placeholder"),
        )


# --------------------------------------------------------------------------- #
# interaction.ts
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptInteraction:
    interaction_id: InteractionId
    interaction_kind: str  # 'approval' | 'question'
    tool_call_id: Optional[str] = None
    state: str = "pending"
    request: Any = None
    response: Any = None

    def to_dict(self) -> dict:
        d: dict = {"interactionId": self.interaction_id, "interactionKind": self.interaction_kind, "state": self.state}
        if self.tool_call_id is not None:
            d["toolCallId"] = self.tool_call_id
        if self.request is not None:
            d["request"] = self.request
        if self.response is not None:
            d["response"] = self.response
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptInteraction":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("interaction must be an object")
        iid = d.get("interactionId")
        kind = d.get("interactionKind")
        if not isinstance(iid, str) or not isinstance(kind, str):
            raise TranscriptDecodeError("interaction requires interactionId and interactionKind")
        return cls(
            interaction_id=iid,
            interaction_kind=kind,
            tool_call_id=d.get("toolCallId"),
            state=d.get("state", "pending"),
            request=d.get("request"),
            response=d.get("response"),
        )


# --------------------------------------------------------------------------- #
# todo.ts
# --------------------------------------------------------------------------- #


@dataclass
class TodoItem:
    title: str
    status: str  # 'pending' | 'in_progress' | 'done'

    def to_dict(self) -> dict:
        return {"title": self.title, "status": self.status}

    @classmethod
    def from_dict(cls, d: Any) -> "TodoItem":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("todo item must be an object")
        title = d.get("title")
        status = d.get("status")
        if not isinstance(title, str) or not isinstance(status, str):
            raise TranscriptDecodeError("todo item requires title and status")
        return cls(title=title, status=status)


@dataclass
class TranscriptTodo:
    todo_id: TodoId
    items: list[TodoItem] = field(default_factory=list)
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {"todoId": self.todo_id, "items": [i.to_dict() for i in self.items]}
        if self.updated_at is not None:
            d["updatedAt"] = self.updated_at
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptTodo":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("todo must be an object")
        tid = d.get("todoId")
        if not isinstance(tid, str):
            raise TranscriptDecodeError("todo requires string todoId")
        raw_items = d.get("items", [])
        if not isinstance(raw_items, list):
            raise TranscriptDecodeError("todo items must be a list")
        return cls(
            todo_id=tid,
            items=[TodoItem.from_dict(i) for i in raw_items],
            updated_at=d.get("updatedAt"),
        )


# --------------------------------------------------------------------------- #
# prompt.ts
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptPrompt:
    prompt_id: PromptId
    status: str
    created_at: str
    user_message_id: Optional[str] = None
    content: Any = None
    finished_at: Optional[str] = None
    steered_at: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {"promptId": self.prompt_id, "status": self.status, "createdAt": self.created_at}
        if self.user_message_id is not None:
            d["userMessageId"] = self.user_message_id
        if self.content is not None:
            d["content"] = self.content
        if self.finished_at is not None:
            d["finishedAt"] = self.finished_at
        if self.steered_at is not None:
            d["steeredAt"] = self.steered_at
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptPrompt":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("prompt must be an object")
        pid = d.get("promptId")
        status = d.get("status")
        created = d.get("createdAt")
        if not isinstance(pid, str) or not isinstance(status, str) or not isinstance(created, str):
            raise TranscriptDecodeError("prompt requires promptId, status and createdAt")
        return cls(
            prompt_id=pid,
            status=status,
            created_at=created,
            user_message_id=d.get("userMessageId"),
            content=d.get("content"),
            finished_at=d.get("finishedAt"),
            steered_at=d.get("steeredAt"),
        )


# --------------------------------------------------------------------------- #
# task.ts (depends on turn.py StepUsage)
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptTask:
    task_id: TaskId
    kind: str  # 'shell' | 'subagent' | 'tool' | 'other'
    state: str
    detached: bool
    description: Optional[str] = None
    agent_id: Optional[str] = None
    output_tail: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    result_summary: Optional[str] = None
    error: Optional[str] = None
    state_reason: Optional[str] = None
    usage: Any = None  # StepUsage
    model: Optional[str] = None
    thinking_effort: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {
            "taskId": self.task_id,
            "kind": self.kind,
            "state": self.state,
            "detached": self.detached,
            "outputTail": self.output_tail,
        }
        if self.description is not None:
            d["description"] = self.description
        if self.agent_id is not None:
            d["agentId"] = self.agent_id
        if self.started_at is not None:
            d["startedAt"] = self.started_at
        if self.ended_at is not None:
            d["endedAt"] = self.ended_at
        if self.result_summary is not None:
            d["resultSummary"] = self.result_summary
        if self.error is not None:
            d["error"] = self.error
        if self.state_reason is not None:
            d["stateReason"] = self.state_reason
        if self.usage is not None:
            d["usage"] = self.usage
        if self.model is not None:
            d["model"] = self.model
        if self.thinking_effort is not None:
            d["thinkingEffort"] = self.thinking_effort
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptTask":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("task must be an object")
        tid = d.get("taskId")
        kind = d.get("kind")
        state = d.get("state")
        detached = d.get("detached")
        if not isinstance(tid, str) or not isinstance(kind, str) or not isinstance(state, str):
            raise TranscriptDecodeError("task requires taskId, kind and state")
        if not isinstance(detached, bool):
            raise TranscriptDecodeError("task.detached must be a boolean")
        return cls(
            task_id=tid,
            kind=kind,
            state=state,
            detached=detached,
            description=d.get("description"),
            agent_id=d.get("agentId"),
            output_tail=d.get("outputTail", ""),
            started_at=d.get("startedAt"),
            ended_at=d.get("endedAt"),
            result_summary=d.get("resultSummary"),
            error=d.get("error"),
            state_reason=d.get("stateReason"),
            usage=StepUsage.from_dict(d["usage"]) if d.get("usage") is not None else None,
            model=d.get("model"),
            thinking_effort=d.get("thinkingEffort"),
        )


# --------------------------------------------------------------------------- #
# turn.ts
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptUsage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    cost: Optional[float] = None

    def to_dict(self) -> dict:
        d: dict = {}
        if self.input_tokens is not None:
            d["inputTokens"] = self.input_tokens
        if self.output_tokens is not None:
            d["outputTokens"] = self.output_tokens
        if self.cached_tokens is not None:
            d["cachedTokens"] = self.cached_tokens
        if self.cost is not None:
            d["cost"] = self.cost
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptUsage":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            raise TranscriptDecodeError("usage must be an object")
        return cls(
            input_tokens=d.get("inputTokens"),
            output_tokens=d.get("outputTokens"),
            cached_tokens=d.get("cachedTokens"),
            cost=d.get("cost"),
        )


@dataclass
class StepUsage:
    input_other: int = 0
    output: int = 0
    input_cache_read: int = 0
    input_cache_creation: int = 0

    def to_dict(self) -> dict:
        return {
            "inputOther": self.input_other,
            "output": self.output,
            "inputCacheRead": self.input_cache_read,
            "inputCacheCreation": self.input_cache_creation,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "StepUsage":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("step usage must be an object")
        return cls(
            input_other=d.get("inputOther", 0),
            output=d.get("output", 0),
            input_cache_read=d.get("inputCacheRead", 0),
            input_cache_creation=d.get("inputCacheCreation", 0),
        )


@dataclass
class StepTiming:
    llm_first_token_latency_ms: Optional[int] = None
    llm_stream_duration_ms: Optional[int] = None
    llm_request_build_ms: Optional[int] = None
    llm_server_first_token_ms: Optional[int] = None
    llm_server_decode_ms: Optional[int] = None
    llm_client_consume_ms: Optional[int] = None

    def to_dict(self) -> dict:
        d: dict = {}
        mapping = {
            "llmFirstTokenLatencyMs": self.llm_first_token_latency_ms,
            "llmStreamDurationMs": self.llm_stream_duration_ms,
            "llmRequestBuildMs": self.llm_request_build_ms,
            "llmServerFirstTokenMs": self.llm_server_first_token_ms,
            "llmServerDecodeMs": self.llm_server_decode_ms,
            "llmClientConsumeMs": self.llm_client_consume_ms,
        }
        for k, v in mapping.items():
            if v is not None:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "StepTiming":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("step timing must be an object")
        return cls(
            llm_first_token_latency_ms=d.get("llmFirstTokenLatencyMs"),
            llm_stream_duration_ms=d.get("llmStreamDurationMs"),
            llm_request_build_ms=d.get("llmRequestBuildMs"),
            llm_server_first_token_ms=d.get("llmServerFirstTokenMs"),
            llm_server_decode_ms=d.get("llmServerDecodeMs"),
            llm_client_consume_ms=d.get("llmClientConsumeMs"),
        )


@dataclass
class StepRetry:
    failed_attempt: int = 0
    next_attempt: int = 0
    max_attempts: int = 0
    delay_ms: int = 0
    error_name: str = ""
    error_message: str = ""
    status_code: Optional[int] = None

    def to_dict(self) -> dict:
        d: dict = {
            "failedAttempt": self.failed_attempt,
            "nextAttempt": self.next_attempt,
            "maxAttempts": self.max_attempts,
            "delayMs": self.delay_ms,
            "errorName": self.error_name,
            "errorMessage": self.error_message,
        }
        if self.status_code is not None:
            d["statusCode"] = self.status_code
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "StepRetry":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("step retry must be an object")
        return cls(
            failed_attempt=d.get("failedAttempt", 0),
            next_attempt=d.get("nextAttempt", 0),
            max_attempts=d.get("maxAttempts", 0),
            delay_ms=d.get("delayMs", 0),
            error_name=d.get("errorName", ""),
            error_message=d.get("errorMessage", ""),
            status_code=d.get("statusCode"),
        )


# --------------------------------------------------------------------------- #
# frame.ts
# --------------------------------------------------------------------------- #


@dataclass
class TextFrame:
    frame_id: FrameId
    role: str  # 'assistant' | 'user'
    text: str = ""
    attachment_ids: Optional[list[AttachmentId]] = None
    task_id: Optional[TaskId] = None
    prompt_ids: Optional[list[str]] = None
    kind: str = "text"

    def to_dict(self) -> dict:
        d: dict = {"kind": "text", "frameId": self.frame_id, "role": self.role, "text": self.text}
        if self.attachment_ids is not None:
            d["attachmentIds"] = list(self.attachment_ids)
        if self.task_id is not None:
            d["taskId"] = self.task_id
        if self.prompt_ids is not None:
            d["promptIds"] = list(self.prompt_ids)
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TextFrame":
        return cls(
            frame_id=d["frameId"],
            role=d["role"],
            text=d.get("text", ""),
            attachment_ids=d.get("attachmentIds"),
            task_id=d.get("taskId"),
            prompt_ids=d.get("promptIds"),
        )


@dataclass
class ThinkingFrame:
    frame_id: FrameId
    text: str = ""
    kind: str = "thinking"

    def to_dict(self) -> dict:
        return {"kind": "thinking", "frameId": self.frame_id, "text": self.text}

    @classmethod
    def from_dict(cls, d: Any) -> "ThinkingFrame":
        return cls(frame_id=d["frameId"], text=d.get("text", ""))


@dataclass
class ToolFrameProgress:
    kind: str  # 'stdout' | 'stderr' | 'progress' | 'status' | 'custom'
    text: Optional[str] = None
    percent: Optional[float] = None
    custom_kind: Optional[str] = None
    custom_data: Any = None

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind}
        if self.text is not None:
            d["text"] = self.text
        if self.percent is not None:
            d["percent"] = self.percent
        if self.custom_kind is not None:
            d["customKind"] = self.custom_kind
        if self.custom_data is not None:
            d["customData"] = self.custom_data
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "ToolFrameProgress":
        return cls(
            kind=d["kind"],
            text=d.get("text"),
            percent=d.get("percent"),
            custom_kind=d.get("customKind"),
            custom_data=d.get("customData"),
        )


@dataclass
class AgentRef:
    agent_id: str
    role: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {"agentId": self.agent_id}
        if self.role is not None:
            d["role"] = self.role
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "AgentRef":
        return cls(agent_id=d["agentId"], role=d.get("role"))


@dataclass
class ToolCallFrame:
    frame_id: FrameId
    tool_call_id: str
    name: str
    state: str = "running"
    view: Optional[str] = None
    input: Any = None
    output: Any = None
    display: Any = None
    error: Optional[str] = None
    input_text: Optional[str] = None
    progress: Optional[ToolFrameProgress] = None
    task_id: Optional[TaskId] = None
    approval_id: Optional[InteractionId] = None
    todo_id: Optional[TodoId] = None
    agent_refs: Optional[list[AgentRef]] = None
    kind: str = "tool"

    def to_dict(self) -> dict:
        d: dict = {
            "kind": "tool",
            "frameId": self.frame_id,
            "toolCallId": self.tool_call_id,
            "name": self.name,
            "state": self.state,
        }
        if self.view is not None:
            d["view"] = self.view
        if self.input is not None:
            d["input"] = self.input
        if self.output is not None:
            d["output"] = self.output
        if self.display is not None:
            d["display"] = self.display
        if self.error is not None:
            d["error"] = self.error
        if self.input_text is not None:
            d["inputText"] = self.input_text
        if self.progress is not None:
            d["progress"] = self.progress.to_dict()
        if self.task_id is not None:
            d["taskId"] = self.task_id
        if self.approval_id is not None:
            d["approvalId"] = self.approval_id
        if self.todo_id is not None:
            d["todoId"] = self.todo_id
        if self.agent_refs is not None:
            d["agentRefs"] = [r.to_dict() for r in self.agent_refs]
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "ToolCallFrame":
        progress = d.get("progress")
        agent_refs = d.get("agentRefs")
        return cls(
            frame_id=d["frameId"],
            tool_call_id=d["toolCallId"],
            name=d["name"],
            state=d.get("state", "running"),
            view=d.get("view"),
            input=d.get("input"),
            output=d.get("output"),
            display=d.get("display"),
            error=d.get("error"),
            input_text=d.get("inputText"),
            progress=ToolFrameProgress.from_dict(progress) if isinstance(progress, dict) else None,
            task_id=d.get("taskId"),
            approval_id=d.get("approvalId"),
            todo_id=d.get("todoId"),
            agent_refs=[AgentRef.from_dict(r) for r in agent_refs] if isinstance(agent_refs, list) else None,
        )


@dataclass
class NoticeFrame:
    frame_id: FrameId
    level: str  # 'error' | 'warning' | 'info'
    message: str = ""
    source: Optional[str] = None
    detail: Any = None
    kind: str = "notice"

    def to_dict(self) -> dict:
        d: dict = {"kind": "notice", "frameId": self.frame_id, "level": self.level, "message": self.message}
        if self.source is not None:
            d["source"] = self.source
        if self.detail is not None:
            d["detail"] = self.detail
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "NoticeFrame":
        return cls(
            frame_id=d["frameId"],
            level=d["level"],
            message=d.get("message", ""),
            source=d.get("source"),
            detail=d.get("detail"),
        )


Frame = Any  # union of the frame dataclasses
TranscriptFrame = Any  # exported name matching the TypeScript union


def frame_to_dict(frame: Any) -> dict:
    return frame.to_dict()


def frame_from_dict(d: Any) -> Any:
    if not isinstance(d, dict):
        raise TranscriptDecodeError("frame must be an object")
    kind = d.get("kind")
    if kind == "text":
        return TextFrame.from_dict(d)
    if kind == "thinking":
        return ThinkingFrame.from_dict(d)
    if kind == "tool":
        return ToolCallFrame.from_dict(d)
    if kind == "notice":
        return NoticeFrame.from_dict(d)
    raise TranscriptDecodeError(f"unknown frame kind: {kind!r}")


# --------------------------------------------------------------------------- #
# turn.ts (continued): TranscriptStep / TranscriptTurn
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptStep:
    step_id: StepId
    turn_id: TurnId
    ordinal: int = 0
    state: str = "running"
    frames: list = field(default_factory=list)  # list[Frame]
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    usage: Optional[StepUsage] = None
    finish_reason: Optional[str] = None
    timing: Optional[StepTiming] = None
    retry: Optional[StepRetry] = None
    end_reason: Optional[str] = None
    end_message: Optional[str] = None
    kind: str = "step"

    def to_dict(self) -> dict:
        d: dict = {
            "kind": "step",
            "stepId": self.step_id,
            "turnId": self.turn_id,
            "ordinal": self.ordinal,
            "state": self.state,
            "frames": [frame_to_dict(f) for f in self.frames],
        }
        if self.started_at is not None:
            d["startedAt"] = self.started_at
        if self.ended_at is not None:
            d["endedAt"] = self.ended_at
        if self.usage is not None:
            d["usage"] = self.usage.to_dict()
        if self.finish_reason is not None:
            d["finishReason"] = self.finish_reason
        if self.timing is not None:
            d["timing"] = self.timing.to_dict()
        if self.retry is not None:
            d["retry"] = self.retry.to_dict()
        if self.end_reason is not None:
            d["endReason"] = self.end_reason
        if self.end_message is not None:
            d["endMessage"] = self.end_message
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptStep":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("step must be an object")
        raw_frames = d.get("frames", [])
        if not isinstance(raw_frames, list):
            raise TranscriptDecodeError("step frames must be a list")
        return cls(
            step_id=d["stepId"],
            turn_id=d["turnId"],
            ordinal=d.get("ordinal", 0),
            state=d.get("state", "running"),
            frames=[frame_from_dict(f) for f in raw_frames],
            started_at=d.get("startedAt"),
            ended_at=d.get("endedAt"),
            usage=StepUsage.from_dict(d["usage"]) if d.get("usage") is not None else None,
            finish_reason=d.get("finishReason"),
            timing=StepTiming.from_dict(d["timing"]) if d.get("timing") is not None else None,
            retry=StepRetry.from_dict(d["retry"]) if d.get("retry") is not None else None,
            end_reason=d.get("endReason"),
            end_message=d.get("endMessage"),
        )


@dataclass
class TranscriptTurn:
    turn_id: TurnId
    ordinal: int = 0
    state: str = "running"
    origin: Any = None  # TurnOrigin (dict)
    prompt: Optional[str] = None
    attachment_ids: Optional[list[AttachmentId]] = None
    steps: list[TranscriptStep] = field(default_factory=list)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    usage: Optional[TranscriptUsage] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    kind: str = "turn"

    def to_dict(self) -> dict:
        d: dict = {
            "kind": "turn",
            "turnId": self.turn_id,
            "ordinal": self.ordinal,
            "state": self.state,
            "origin": self.origin if self.origin is not None else {"kind": "other"},
            "steps": [s.to_dict() for s in self.steps],
        }
        if self.prompt is not None:
            d["prompt"] = self.prompt
        if self.attachment_ids is not None:
            d["attachmentIds"] = list(self.attachment_ids)
        if self.started_at is not None:
            d["startedAt"] = self.started_at
        if self.ended_at is not None:
            d["endedAt"] = self.ended_at
        if self.usage is not None:
            d["usage"] = self.usage.to_dict()
        if self.duration_ms is not None:
            d["durationMs"] = self.duration_ms
        if self.error is not None:
            d["error"] = self.error
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptTurn":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("turn must be an object")
        raw_steps = d.get("steps", [])
        if not isinstance(raw_steps, list):
            raise TranscriptDecodeError("turn steps must be a list")
        return cls(
            turn_id=d["turnId"],
            ordinal=d.get("ordinal", 0),
            state=d.get("state", "running"),
            origin=d.get("origin", {"kind": "other"}),
            prompt=d.get("prompt"),
            attachment_ids=d.get("attachmentIds"),
            steps=[TranscriptStep.from_dict(s) for s in raw_steps],
            started_at=d.get("startedAt"),
            ended_at=d.get("endedAt"),
            usage=TranscriptUsage.from_dict(d["usage"]) if d.get("usage") is not None else None,
            duration_ms=d.get("durationMs"),
            error=d.get("error"),
        )


# --------------------------------------------------------------------------- #
# item.ts
# --------------------------------------------------------------------------- #

KNOWN_MARKERS = [
    "compaction",
    "undo",
    "clear",
    "goal",
    "plan.enter",
    "plan.exit",
    "plan.revision",
    "swarm.enter",
    "swarm.exit",
    "skill",
    "cron.fired",
    "notice",
    "hook",
]


@dataclass
class TranscriptMarker:
    marker_id: MarkerId
    marker: str
    payload: Any = None
    at: Optional[str] = None
    kind: str = "marker"

    def to_dict(self) -> dict:
        d: dict = {"kind": "marker", "markerId": self.marker_id, "marker": self.marker}
        if self.payload is not None:
            d["payload"] = self.payload
        if self.at is not None:
            d["at"] = self.at
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptMarker":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("marker must be an object")
        return cls(
            marker_id=d["markerId"],
            marker=d["marker"],
            payload=d.get("payload"),
            at=d.get("at"),
        )


@dataclass
class TranscriptTaskRef:
    ref_id: TaskRefId
    task_id: TaskId
    at: Optional[str] = None
    kind: str = "taskref"

    def to_dict(self) -> dict:
        d: dict = {"kind": "taskref", "refId": self.ref_id, "taskId": self.task_id}
        if self.at is not None:
            d["at"] = self.at
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptTaskRef":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("taskref must be an object")
        return cls(ref_id=d["refId"], task_id=d["taskId"], at=d.get("at"))


TranscriptItem = Any  # union of turn | marker | taskref


def item_to_dict(item: Any) -> dict:
    return item.to_dict()


def item_from_dict(d: Any) -> Any:
    if not isinstance(d, dict):
        raise TranscriptDecodeError("item must be an object")
    kind = d.get("kind")
    if kind == "turn":
        return TranscriptTurn.from_dict(d)
    if kind == "marker":
        return TranscriptMarker.from_dict(d)
    if kind == "taskref":
        return TranscriptTaskRef.from_dict(d)
    raise TranscriptDecodeError(f"unknown item kind: {kind!r}")


def item_id(item: Any) -> str:
    return item.turn_id if item.kind == "turn" else item.marker_id if item.kind == "marker" else item.ref_id


# --------------------------------------------------------------------------- #
# meta.ts
# --------------------------------------------------------------------------- #


@dataclass
class GoalMeta:
    objective: str
    status: str  # 'active' | 'paused' | 'blocked' | 'complete'
    completion_criterion: Optional[str] = None
    budget_used: Optional[float] = None
    budget_limit: Optional[float] = None

    def to_dict(self) -> dict:
        d: dict = {"objective": self.objective, "status": self.status}
        if self.completion_criterion is not None:
            d["completionCriterion"] = self.completion_criterion
        if self.budget_used is not None:
            d["budgetUsed"] = self.budget_used
        if self.budget_limit is not None:
            d["budgetLimit"] = self.budget_limit
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "GoalMeta":
        if not isinstance(d, dict):
            raise TranscriptDecodeError("goal meta must be an object")
        return cls(
            objective=d.get("objective", ""),
            status=d.get("status", "active"),
            completion_criterion=d.get("completionCriterion"),
            budget_used=d.get("budgetUsed"),
            budget_limit=d.get("budgetLimit"),
        )


@dataclass
class ModesMeta:
    plan: Optional[dict] = None
    swarm: Optional[dict] = None
    tower: Optional[dict] = None

    def to_dict(self) -> dict:
        d: dict = {}
        if self.plan is not None:
            d["plan"] = self.plan
        if self.swarm is not None:
            d["swarm"] = self.swarm
        if self.tower is not None:
            d["tower"] = self.tower
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "ModesMeta":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            raise TranscriptDecodeError("modes meta must be an object")
        return cls(plan=d.get("plan"), swarm=d.get("swarm"), tower=d.get("tower"))


@dataclass
class AgentUsageMeta:
    by_model: Optional[dict] = None
    current_turn: Optional[StepUsage] = None
    total: Optional[StepUsage] = None

    def to_dict(self) -> dict:
        d: dict = {}
        if self.by_model is not None:
            d["byModel"] = self.by_model
        if self.current_turn is not None:
            d["currentTurn"] = self.current_turn.to_dict()
        if self.total is not None:
            d["total"] = self.total.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "AgentUsageMeta":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            raise TranscriptDecodeError("agent usage meta must be an object")
        cur = d.get("currentTurn")
        tot = d.get("total")
        return cls(
            by_model=d.get("byModel"),
            current_turn=StepUsage.from_dict(cur) if cur is not None else None,
            total=StepUsage.from_dict(tot) if tot is not None else None,
        )


@dataclass
class AgentStatusMeta:
    model: Optional[str] = None
    thinking_effort: Optional[str] = None
    usage: Optional[AgentUsageMeta] = None
    context_tokens: Optional[int] = None
    max_context_tokens: Optional[int] = None
    context_usage: Optional[float] = None
    permission: Optional[str] = None
    phase: Any = None  # discriminated union (dict)

    def to_dict(self) -> dict:
        d: dict = {}
        if self.model is not None:
            d["model"] = self.model
        if self.thinking_effort is not None:
            d["thinkingEffort"] = self.thinking_effort
        if self.usage is not None:
            d["usage"] = self.usage.to_dict()
        if self.context_tokens is not None:
            d["contextTokens"] = self.context_tokens
        if self.max_context_tokens is not None:
            d["maxContextTokens"] = self.max_context_tokens
        if self.context_usage is not None:
            d["contextUsage"] = self.context_usage
        if self.permission is not None:
            d["permission"] = self.permission
        if self.phase is not None:
            d["phase"] = self.phase
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "AgentStatusMeta":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            raise TranscriptDecodeError("agent status meta must be an object")
        return cls(
            model=d.get("model"),
            thinking_effort=d.get("thinkingEffort"),
            usage=AgentUsageMeta.from_dict(d["usage"]) if d.get("usage") is not None else None,
            context_tokens=d.get("contextTokens"),
            max_context_tokens=d.get("maxContextTokens"),
            context_usage=d.get("contextUsage"),
            permission=d.get("permission"),
            phase=d.get("phase"),
        )


@dataclass
class TranscriptMeta:
    goal: Optional[GoalMeta] = None
    modes: Optional[ModesMeta] = None
    activity: Optional[str] = None
    agent: Optional[AgentStatusMeta] = None

    def to_dict(self) -> dict:
        d: dict = {}
        if self.goal is not None:
            d["goal"] = self.goal.to_dict()
        if self.modes is not None:
            d["modes"] = self.modes.to_dict()
        if self.activity is not None:
            d["activity"] = self.activity
        if self.agent is not None:
            d["agent"] = self.agent.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TranscriptMeta":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            raise TranscriptDecodeError("meta must be an object")
        return cls(
            goal=GoalMeta.from_dict(d["goal"]) if d.get("goal") is not None else None,
            modes=ModesMeta.from_dict(d["modes"]) if d.get("modes") is not None else None,
            activity=d.get("activity"),
            agent=AgentStatusMeta.from_dict(d["agent"]) if d.get("agent") is not None else None,
        )
