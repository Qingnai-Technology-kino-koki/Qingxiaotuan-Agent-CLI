"""Agent / session event type definitions.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

The TS source defines ~80 event variants and validates them via zod. Here we
port the *type definitions* (enums + dataclasses) for the core, self-contained
event families and expose :func:`parse_event`, which dispatches on the wire
``type``. Cross-module payloads (``Session``, ``Workspace``, ``ConfigResponse``,
provider refresh diffs) are intentionally treated as opaque ``dict`` values —
those heavy downstream models live in ``session.ts`` / ``workspace.ts`` /
``rest/config.ts`` / ``modelCatalog.ts``, which are out of the foundational
scope (see ``SKIPPED.md``).

Construction is lenient: every event field defaults to ``None`` and
``from_dict`` copies the wire dict through, so unknown/forward-compatible
fields survive a round-trip. ``to_dict`` omits ``None`` to match the zod
``undefined``-drops-on-serialize behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .message import parse_message_content  # noqa: F401 (re-export parity)


# --- Enums & shared value types ------------------------------------------------


class FinishReason(str, Enum):
    COMPLETED = "completed"
    TOOL_CALLS = "tool_calls"
    TRUNCATED = "truncated"
    FILTERED = "filtered"
    PAUSED = "paused"
    OTHER = "other"


class PermissionMode(str, Enum):
    MANUAL = "manual"
    YOLO = "yolo"
    AUTO = "auto"


class SkillSource(str, Enum):
    PROJECT = "project"
    USER = "user"
    EXTRA = "extra"
    BUILTIN = "builtin"


class TaskLifecycleStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    KILLED = "killed"
    LOST = "lost"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETE = "complete"


class GoalActor(str, Enum):
    USER = "user"
    MODEL = "model"
    RUNTIME = "runtime"
    SYSTEM = "system"


class TurnEndReason(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"


class TurnInterruptReason(str, Enum):
    USER_CANCELLED = "user_cancelled"
    ABORTED = "aborted"
    MAX_STEPS = "max_steps"
    ERROR = "error"
    FILTERED = "filtered"
    BLOCKED = "blocked"


class ToolUpdateKind(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"
    PROGRESS = "progress"
    STATUS = "status"
    CUSTOM = "custom"


@dataclass
class TokenUsage:
    input_other: int = 0
    output: int = 0
    input_cache_read: int = 0
    input_cache_creation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputOther": self.input_other,
            "output": self.output,
            "inputCacheRead": self.input_cache_read,
            "inputCacheCreation": self.input_cache_creation,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TokenUsage":
        return cls(
            input_other=raw.get("inputOther", 0),
            output=raw.get("output", 0),
            input_cache_read=raw.get("inputCacheRead", 0),
            input_cache_creation=raw.get("inputCacheCreation", 0),
        )


@dataclass
class UsageStatus:
    by_model: Optional[dict[str, TokenUsage]] = None
    current_turn: Optional[TokenUsage] = None
    total: Optional[TokenUsage] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.by_model is not None:
            out["byModel"] = {k: v.to_dict() for k, v in self.by_model.items()}
        if self.current_turn is not None:
            out["currentTurn"] = self.current_turn.to_dict()
        if self.total is not None:
            out["total"] = self.total.to_dict()
        return out


# --- Event base & helpers -----------------------------------------------------


@dataclass
class _EventBase:
    """Base providing lenient ``to_dict`` (drops ``None``) for events."""

    type: str = ""

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {k: v for k, v in asdict(self).items() if v is not None}


def _from_dict(cls: type, raw: dict[str, Any]) -> Any:
    from dataclasses import fields

    kwargs: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name == "type":
            kwargs["type"] = raw.get("type", f.default)
        else:
            kwargs[f.name] = raw.get(f.name)
    return cls(**kwargs)  # type: ignore[call-arg]


# --- Event dataclasses (core, self-contained families) ------------------------


@dataclass
class ErrorEvent(_EventBase):
    type: str = "error"
    code: str = ""
    message: str = ""
    name: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    retryable: bool = False
    cause: Optional[dict[str, Any]] = None


@dataclass
class WarningEvent(_EventBase):
    type: str = "warning"
    message: str = ""
    code: Optional[str] = None


@dataclass
class AgentStatusUpdatedEvent(_EventBase):
    type: str = "agent.status.updated"
    model: Optional[str] = None
    thinking_effort: Optional[str] = None
    context_tokens: Optional[int] = None
    max_context_tokens: Optional[int] = None
    context_usage: Optional[float] = None
    plan_mode: Optional[bool] = None
    swarm_mode: Optional[bool] = None
    tower_mode: Optional[bool] = None
    permission: Optional[str] = None
    usage: Optional[dict[str, Any]] = None
    phase: Optional[dict[str, Any]] = None


@dataclass
class SessionMetaUpdatedEvent(_EventBase):
    type: str = "session.meta.updated"
    title: Optional[str] = None
    patch: Optional[dict[str, Any]] = None


@dataclass
class TurnStartedEvent(_EventBase):
    type: str = "turn.started"
    turn_id: Optional[int] = None
    origin: Optional[dict[str, Any]] = None
    prompt: Optional[str] = None
    prompt_id: Optional[str] = None
    prompt_attachments: Optional[list[dict[str, Any]]] = None


@dataclass
class TurnEndedEvent(_EventBase):
    type: str = "turn.ended"
    turn_id: Optional[int] = None
    time: Optional[int] = None
    reason: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    duration_ms: Optional[int] = None
    interrupt_reason: Optional[str] = None


@dataclass
class TurnStepStartedEvent(_EventBase):
    type: str = "turn.step.started"
    turn_id: Optional[int] = None
    step: Optional[int] = None
    step_id: Optional[str] = None


@dataclass
class TurnStepCompletedEvent(_EventBase):
    type: str = "turn.step.completed"
    turn_id: Optional[int] = None
    step: Optional[int] = None
    step_id: Optional[str] = None
    usage: Optional[dict[str, Any]] = None
    finish_reason: Optional[str] = None
    llm_first_token_latency_ms: Optional[int] = None
    llm_stream_duration_ms: Optional[int] = None
    provider_finish_reason: Optional[str] = None
    raw_finish_reason: Optional[str] = None


@dataclass
class TurnStepRetryingEvent(_EventBase):
    type: str = "turn.step.retrying"
    turn_id: Optional[int] = None
    step: Optional[int] = None
    step_id: Optional[str] = None
    failed_attempt: Optional[int] = None
    next_attempt: Optional[int] = None
    max_attempts: Optional[int] = None
    delay_ms: Optional[int] = None
    error_name: Optional[str] = None
    error_message: Optional[str] = None
    status_code: Optional[int] = None


@dataclass
class TurnStepInterruptedEvent(_EventBase):
    type: str = "turn.step.interrupted"
    turn_id: Optional[int] = None
    step: Optional[int] = None
    step_id: Optional[str] = None
    reason: Optional[str] = None
    message: Optional[str] = None


@dataclass
class AssistantDeltaEvent(_EventBase):
    type: str = "assistant.delta"
    turn_id: Optional[int] = None
    delta: Optional[str] = None


@dataclass
class ThinkingDeltaEvent(_EventBase):
    type: str = "thinking.delta"
    turn_id: Optional[int] = None
    delta: Optional[str] = None


@dataclass
class ToolCallDeltaEvent(_EventBase):
    type: str = "tool.call.delta"
    turn_id: Optional[int] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    arguments_part: Optional[str] = None


@dataclass
class ToolCallStartedEvent(_EventBase):
    type: str = "tool.call.started"
    turn_id: Optional[int] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    args: Optional[Any] = None
    description: Optional[str] = None
    display: Optional[dict[str, Any]] = None


@dataclass
class ToolProgressEvent(_EventBase):
    type: str = "tool.progress"
    turn_id: Optional[int] = None
    tool_call_id: Optional[str] = None
    update: Optional[dict[str, Any]] = None


@dataclass
class ToolResultEvent(_EventBase):
    type: str = "tool.result"
    turn_id: Optional[int] = None
    tool_call_id: Optional[str] = None
    output: Optional[Any] = None
    is_error: Optional[bool] = None
    synthetic: Optional[bool] = None


@dataclass
class ShellOutputEvent(_EventBase):
    type: str = "shell.output"
    command_id: Optional[str] = None
    update: Optional[dict[str, Any]] = None
    task_id: Optional[str] = None


@dataclass
class ShellStartedEvent(_EventBase):
    type: str = "shell.started"
    command_id: Optional[str] = None
    task_id: Optional[str] = None


@dataclass
class ShellCompletedEvent(_EventBase):
    type: str = "shell.completed"
    command_id: Optional[str] = None
    is_error: Optional[bool] = None
    task_id: Optional[str] = None


@dataclass
class SubagentSpawnedEvent(_EventBase):
    type: str = "subagent.spawned"
    subagent_id: Optional[str] = None
    subagent_name: Optional[str] = None
    parent_tool_call_id: Optional[str] = None
    parent_tool_call_uuid: Optional[str] = None
    parent_agent_id: Optional[str] = None
    caller_agent_id: Optional[str] = None
    description: Optional[str] = None
    swarm_index: Optional[int] = None
    run_in_background: Optional[bool] = None
    model: Optional[str] = None
    thinking_effort: Optional[str] = None
    task_id: Optional[str] = None


@dataclass
class SubagentStartedEvent(_EventBase):
    type: str = "subagent.started"
    subagent_id: Optional[str] = None


@dataclass
class SubagentSuspendedEvent(_EventBase):
    type: str = "subagent.suspended"
    subagent_id: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class SubagentCompletedEvent(_EventBase):
    type: str = "subagent.completed"
    subagent_id: Optional[str] = None
    result_summary: Optional[str] = None
    usage: Optional[dict[str, Any]] = None
    context_tokens: Optional[int] = None


@dataclass
class SubagentFailedEvent(_EventBase):
    type: str = "subagent.failed"
    subagent_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class TaskStartedEvent(_EventBase):
    type: str = "task.started"
    info: Optional[dict[str, Any]] = None


@dataclass
class TaskTerminatedEvent(_EventBase):
    type: str = "task.terminated"
    info: Optional[dict[str, Any]] = None


@dataclass
class CronFiredEvent(_EventBase):
    type: str = "cron.fired"
    origin: Optional[dict[str, Any]] = None
    prompt: Optional[str] = None


@dataclass
class PromptSubmittedEvent(_EventBase):
    type: str = "prompt.submitted"
    prompt_id: Optional[str] = None
    user_message_id: Optional[str] = None
    status: Optional[str] = None
    content: Optional[list[Any]] = None
    created_at: Optional[str] = None


@dataclass
class PromptCompletedEvent(_EventBase):
    type: str = "prompt.completed"
    prompt_id: Optional[str] = None
    finished_at: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class PromptAbortedEvent(_EventBase):
    type: str = "prompt.aborted"
    prompt_id: Optional[str] = None
    aborted_at: Optional[str] = None


@dataclass
class PromptSteeredEvent(_EventBase):
    type: str = "prompt.steered"
    active_prompt_id: Optional[str] = None
    prompt_ids: Optional[list[str]] = None
    content: Optional[list[Any]] = None
    steered_at: Optional[str] = None


@dataclass
class ToolListUpdatedEvent(_EventBase):
    type: str = "tool.list.updated"
    reason: Optional[str] = None
    server_name: Optional[str] = None


@dataclass
class McpServerStatusEvent(_EventBase):
    type: str = "mcp.server.status"
    server: Optional[dict[str, Any]] = None


@dataclass
class GoalUpdatedEvent(_EventBase):
    type: str = "goal.updated"
    snapshot: Optional[dict[str, Any]] = None
    change: Optional[dict[str, Any]] = None


@dataclass
class SkillActivatedEvent(_EventBase):
    type: str = "skill.activated"
    activation_id: Optional[str] = None
    skill_name: Optional[str] = None
    skill_args: Optional[str] = None
    trigger: Optional[str] = None
    skill_path: Optional[str] = None
    skill_source: Optional[str] = None


@dataclass
class PluginCommandActivatedEvent(_EventBase):
    type: str = "plugin_command.activated"
    activation_id: Optional[str] = None
    plugin_id: Optional[str] = None
    command_name: Optional[str] = None
    command_args: Optional[str] = None
    trigger: Optional[str] = None


@dataclass
class SessionWorkChangedEvent(_EventBase):
    type: str = "event.session.work_changed"
    busy: Optional[bool] = None
    main_turn_active: Optional[bool] = None
    pending_interaction: Optional[Any] = None
    last_turn_reason: Optional[str] = None


@dataclass
class SessionStatusChangedEvent(_EventBase):
    type: str = "event.session.status_changed"
    status: Optional[str] = None
    previous_status: Optional[str] = None
    current_prompt_id: Optional[str] = None


@dataclass
class SessionCreatedEvent(_EventBase):
    type: str = "event.session.created"
    session: Optional[dict[str, Any]] = None


@dataclass
class WorkspaceCreatedEvent(_EventBase):
    type: str = "event.workspace.created"
    workspace: Optional[dict[str, Any]] = None


@dataclass
class WorkspaceUpdatedEvent(_EventBase):
    type: str = "event.workspace.updated"
    workspace: Optional[dict[str, Any]] = None


@dataclass
class WorkspaceDeletedEvent(_EventBase):
    type: str = "event.workspace.deleted"
    workspace_id: Optional[str] = None
    root: Optional[str] = None


@dataclass
class ConfigChangedEvent(_EventBase):
    type: str = "event.config.changed"
    changed_fields: Optional[list[str]] = None
    config: Optional[dict[str, Any]] = None


@dataclass
class ConfigWarningEvent(_EventBase):
    type: str = "event.config.warning"
    warnings: Optional[list[dict[str, Any]]] = None


@dataclass
class ModelCatalogChangedEvent(_EventBase):
    type: str = "event.model_catalog.changed"
    changed: Optional[list[dict[str, Any]]] = None
    unchanged: Optional[list[str]] = None
    failed: Optional[list[dict[str, Any]]] = None


@dataclass
class PluginChangedEvent(_EventBase):
    type: str = "event.plugin.changed"


@dataclass
class CapabilityChangedEvent(_EventBase):
    type: str = "event.capability.changed"
    capability_id: Optional[str] = None
    install: Optional[dict[str, Any]] = None


EVENT_TYPES: dict[str, type] = {
    "error": ErrorEvent,
    "warning": WarningEvent,
    "agent.status.updated": AgentStatusUpdatedEvent,
    "session.meta.updated": SessionMetaUpdatedEvent,
    "turn.started": TurnStartedEvent,
    "turn.ended": TurnEndedEvent,
    "turn.step.started": TurnStepStartedEvent,
    "turn.step.completed": TurnStepCompletedEvent,
    "turn.step.retrying": TurnStepRetryingEvent,
    "turn.step.interrupted": TurnStepInterruptedEvent,
    "assistant.delta": AssistantDeltaEvent,
    "thinking.delta": ThinkingDeltaEvent,
    "tool.call.delta": ToolCallDeltaEvent,
    "tool.call.started": ToolCallStartedEvent,
    "tool.progress": ToolProgressEvent,
    "tool.result": ToolResultEvent,
    "shell.output": ShellOutputEvent,
    "shell.started": ShellStartedEvent,
    "shell.completed": ShellCompletedEvent,
    "subagent.spawned": SubagentSpawnedEvent,
    "subagent.started": SubagentStartedEvent,
    "subagent.suspended": SubagentSuspendedEvent,
    "subagent.completed": SubagentCompletedEvent,
    "subagent.failed": SubagentFailedEvent,
    "task.started": TaskStartedEvent,
    "task.terminated": TaskTerminatedEvent,
    "cron.fired": CronFiredEvent,
    "prompt.submitted": PromptSubmittedEvent,
    "prompt.completed": PromptCompletedEvent,
    "prompt.aborted": PromptAbortedEvent,
    "prompt.steered": PromptSteeredEvent,
    "tool.list.updated": ToolListUpdatedEvent,
    "mcp.server.status": McpServerStatusEvent,
    "goal.updated": GoalUpdatedEvent,
    "skill.activated": SkillActivatedEvent,
    "plugin_command.activated": PluginCommandActivatedEvent,
    "event.session.work_changed": SessionWorkChangedEvent,
    "event.session.status_changed": SessionStatusChangedEvent,
    "event.session.created": SessionCreatedEvent,
    "event.workspace.created": WorkspaceCreatedEvent,
    "event.workspace.updated": WorkspaceUpdatedEvent,
    "event.workspace.deleted": WorkspaceDeletedEvent,
    "event.config.changed": ConfigChangedEvent,
    "event.config.warning": ConfigWarningEvent,
    "event.model_catalog.changed": ModelCatalogChangedEvent,
    "event.plugin.changed": PluginChangedEvent,
    "event.capability.changed": CapabilityChangedEvent,
}


@dataclass
class Event(_EventBase):
    """Generic event fallback for unregistered / forward-compatible types."""

    type: str = ""
    agent_id: Optional[str] = None
    session_id: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Event":
        ev = cls(type=raw.get("type", ""), agent_id=raw.get("agent_id"), session_id=raw.get("session_id"))
        extra = {
            k: v
            for k, v in raw.items()
            if k not in ("type", "agent_id", "session_id")
        }
        ev._extra = extra  # type: ignore[attr-defined]
        return ev

    def to_dict(self) -> dict[str, Any]:
        out = {"type": self.type}
        if self.agent_id is not None:
            out["agent_id"] = self.agent_id
        if self.session_id is not None:
            out["session_id"] = self.session_id
        extra = getattr(self, "_extra", None)
        if extra:
            out.update(extra)
        return out


def parse_event(raw: dict[str, Any]) -> Any:
    """Dispatch on ``type`` to a typed event dataclass, or a generic :class:`Event`."""
    if not isinstance(raw, dict):
        raise ValueError("event must be an object")
    event_type = raw.get("type")
    cls = EVENT_TYPES.get(event_type)
    if cls is not None:
        return _from_dict(cls, raw)
    return Event.from_dict(raw)
