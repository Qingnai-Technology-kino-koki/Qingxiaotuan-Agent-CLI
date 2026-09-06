"""Transcript operations and snapshot type (对齐上游 ops/operation 的语义)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union

from .exceptions import TranscriptDecodeError
from .ids import (
    AgentId,
    FrameId,
    StepId,
    TaskId,
    TurnId,
)
from .model import (
    TranscriptAttachment,
    TranscriptFrame,  # noqa: F401 (re-export)
    TranscriptInteraction,
    TranscriptItem,
    TranscriptMarker,
    TranscriptMeta,
    TranscriptPrompt,
    TranscriptTask,
    TranscriptTaskRef,
    TranscriptTodo,
    TranscriptTurn,
    frame_from_dict,
    frame_to_dict,
    item_from_dict,
    item_to_dict,
)


@dataclass
class AgentTranscriptSnapshot:
    items: list = field(default_factory=list)  # list[TranscriptItem]
    tasks: list = field(default_factory=list)  # list[TranscriptTask]
    interactions: list = field(default_factory=list)  # list[TranscriptInteraction]
    attachments: list = field(default_factory=list)  # list[TranscriptAttachment]
    todos: list = field(default_factory=list)  # list[TranscriptTodo]
    prompts: list = field(default_factory=list)  # list[TranscriptPrompt]
    meta: TranscriptMeta = field(default_factory=TranscriptMeta)
    has_more_older: bool = False


# Operation dataclasses ------------------------------------------------------ #


@dataclass
class ResetOp:
    op: str = "reset"
    agent_id: AgentId = ""
    snapshot: Any = None  # AgentTranscriptSnapshot (set by callers)


@dataclass
class TurnUpsertOp:
    op: str = "turn.upsert"
    turn: Any = None  # TurnHeader (TranscriptTurn minus steps)


@dataclass
class StepUpsertOp:
    op: str = "step.upsert"
    turn_id: TurnId = ""
    step: Any = None  # StepHeader (TranscriptStep minus frames)


@dataclass
class FrameUpsertOp:
    op: str = "frame.upsert"
    turn_id: TurnId = ""
    step_id: StepId = ""
    frame: Any = None


@dataclass
class AppendTargetFrame:
    type: str = "frame"
    turn_id: TurnId = ""
    step_id: StepId = ""
    frame_id: FrameId = ""


@dataclass
class AppendTargetTask:
    type: str = "task"
    task_id: TaskId = ""


AppendTarget = Union[AppendTargetFrame, AppendTargetTask]


@dataclass
class AppendOp:
    op: str = "append"
    target: Any = None  # AppendTarget
    offset: int = 0
    text: str = ""


@dataclass
class MarkerUpsertOp:
    op: str = "marker.upsert"
    item: Any = None  # TranscriptMarker
    before_turn: Optional[int] = None


@dataclass
class TaskRefUpsertOp:
    op: str = "taskref.upsert"
    item: Any = None  # TranscriptTaskRef
    before_turn: Optional[int] = None


@dataclass
class TaskUpsertOp:
    op: str = "task.upsert"
    task: Any = None  # TranscriptTask


@dataclass
class InteractionUpsertOp:
    op: str = "interaction.upsert"
    interaction: Any = None  # TranscriptInteraction


@dataclass
class AttachmentUpsertOp:
    op: str = "attachment.upsert"
    attachment: Any = None  # TranscriptAttachment


@dataclass
class TodoUpsertOp:
    op: str = "todo.upsert"
    todo: Any = None  # TranscriptTodo


@dataclass
class PromptUpsertOp:
    op: str = "prompt.upsert"
    prompt: Any = None  # TranscriptPrompt


@dataclass
class MetaMergeOp:
    op: str = "meta.merge"
    meta: Any = None  # TranscriptMetaMerge (dict)


@dataclass
class ItemsRemoveOp:
    op: str = "items.remove"
    ids: list = field(default_factory=list)


TranscriptOperation = Any  # union of the op dataclasses
Operation = Any  # public alias for a transcript operation


# (de)serialization ----------------------------------------------------------- #


def _target_to_dict(target: Any) -> dict:
    if target.type == "task":
        return {"type": "task", "taskId": target.task_id}
    return {"type": "frame", "turnId": target.turn_id, "stepId": target.step_id, "frameId": target.frame_id}


def _target_from_dict(d: Any) -> Any:
    if not isinstance(d, dict):
        raise TranscriptDecodeError("append target must be an object")
    t = d.get("type")
    if t == "task":
        return AppendTargetTask(task_id=d["taskId"])
    if t == "frame":
        return AppendTargetFrame(turn_id=d["turnId"], step_id=d["stepId"], frame_id=d["frameId"])
    raise TranscriptDecodeError(f"unknown append target type: {t!r}")


def operation_to_dict(op: Any) -> dict:
    name = op.op
    if name == "reset":
        return {"op": "reset", "agentId": op.agent_id, "snapshot": snapshot_to_dict(op.snapshot)}
    if name == "turn.upsert":
        return {"op": "turn.upsert", "turn": item_to_dict(op.turn)}
    if name == "step.upsert":
        return {"op": "step.upsert", "turnId": op.turn_id, "step": op.step.to_dict()}
    if name == "frame.upsert":
        return {"op": "frame.upsert", "turnId": op.turn_id, "stepId": op.step_id, "frame": frame_to_dict(op.frame)}
    if name == "append":
        return {"op": "append", "target": _target_to_dict(op.target), "offset": op.offset, "text": op.text}
    if name == "marker.upsert":
        d = {"op": "marker.upsert", "item": item_to_dict(op.item)}
        if op.before_turn is not None:
            d["beforeTurn"] = op.before_turn
        return d
    if name == "taskref.upsert":
        d = {"op": "taskref.upsert", "item": item_to_dict(op.item)}
        if op.before_turn is not None:
            d["beforeTurn"] = op.before_turn
        return d
    if name == "task.upsert":
        return {"op": "task.upsert", "task": op.task.to_dict()}
    if name == "interaction.upsert":
        return {"op": "interaction.upsert", "interaction": op.interaction.to_dict()}
    if name == "attachment.upsert":
        return {"op": "attachment.upsert", "attachment": op.attachment.to_dict()}
    if name == "todo.upsert":
        return {"op": "todo.upsert", "todo": op.todo.to_dict()}
    if name == "prompt.upsert":
        return {"op": "prompt.upsert", "prompt": op.prompt.to_dict()}
    if name == "meta.merge":
        return {"op": "meta.merge", "meta": op.meta}
    if name == "items.remove":
        return {"op": "items.remove", "ids": list(op.ids)}
    raise TranscriptDecodeError(f"unknown operation: {name!r}")


def operation_from_dict(d: Any) -> Any:
    if not isinstance(d, dict):
        raise TranscriptDecodeError("operation must be an object")
    name = d.get("op")
    if name == "reset":
        return ResetOp(agent_id=d["agentId"], snapshot=snapshot_from_dict(d["snapshot"]))
    if name == "turn.upsert":
        return TurnUpsertOp(turn=item_from_dict(d["turn"]))
    if name == "step.upsert":
        return StepUpsertOp(turn_id=d["turnId"], step=TranscriptStep.from_dict(d["step"]))
    if name == "frame.upsert":
        return FrameUpsertOp(turn_id=d["turnId"], step_id=d["stepId"], frame=frame_from_dict(d["frame"]))
    if name == "append":
        return AppendOp(target=_target_from_dict(d["target"]), offset=d.get("offset", 0), text=d.get("text", ""))
    if name == "marker.upsert":
        return MarkerUpsertOp(item=item_from_dict(d["item"]), before_turn=d.get("beforeTurn"))
    if name == "taskref.upsert":
        return TaskRefUpsertOp(item=item_from_dict(d["item"]), before_turn=d.get("beforeTurn"))
    if name == "task.upsert":
        return TaskUpsertOp(task=TranscriptTask.from_dict(d["task"]))
    if name == "interaction.upsert":
        return InteractionUpsertOp(interaction=TranscriptInteraction.from_dict(d["interaction"]))
    if name == "attachment.upsert":
        return AttachmentUpsertOp(attachment=TranscriptAttachment.from_dict(d["attachment"]))
    if name == "todo.upsert":
        return TodoUpsertOp(todo=TranscriptTodo.from_dict(d["todo"]))
    if name == "prompt.upsert":
        return PromptUpsertOp(prompt=TranscriptPrompt.from_dict(d["prompt"]))
    if name == "meta.merge":
        return MetaMergeOp(meta=d.get("meta"))
    if name == "items.remove":
        return ItemsRemoveOp(ids=list(d.get("ids", [])))
    raise TranscriptDecodeError(f"unknown operation: {name!r}")


# A step header is a TranscriptStep without frames; the apply layer only reads
# its non-frame fields. We reuse the TranscriptStep dataclass (frames default empty).
from .model import TranscriptStep  # noqa: E402


# Snapshot (de)serialization ------------------------------------------------- #


def snapshot_to_dict(snapshot: AgentTranscriptSnapshot) -> dict:
    return {
        "items": [item_to_dict(i) for i in snapshot.items],
        "tasks": [t.to_dict() for t in snapshot.tasks],
        "interactions": [i.to_dict() for i in snapshot.interactions],
        "attachments": [a.to_dict() for a in snapshot.attachments],
        "todos": [t.to_dict() for t in snapshot.todos],
        "prompts": [p.to_dict() for p in snapshot.prompts],
        "meta": snapshot.meta.to_dict(),
        "hasMoreOlder": snapshot.has_more_older,
    }


def snapshot_from_dict(d: Any) -> AgentTranscriptSnapshot:
    if not isinstance(d, dict):
        raise TranscriptDecodeError("snapshot must be an object")
    for key in ("items", "tasks", "interactions", "attachments", "todos", "prompts"):
        if key in d and not isinstance(d[key], list):
            raise TranscriptDecodeError(f"snapshot.{key} must be a list")
    return AgentTranscriptSnapshot(
        items=[item_from_dict(i) for i in d.get("items", [])],
        tasks=[TranscriptTask.from_dict(t) for t in d.get("tasks", [])],
        interactions=[TranscriptInteraction.from_dict(i) for i in d.get("interactions", [])],
        attachments=[TranscriptAttachment.from_dict(a) for a in d.get("attachments", [])],
        todos=[TranscriptTodo.from_dict(t) for t in d.get("todos", [])],
        prompts=[TranscriptPrompt.from_dict(p) for p in d.get("prompts", [])],
        meta=TranscriptMeta.from_dict(d.get("meta")),
        has_more_older=bool(d.get("hasMoreOlder", False)),
    )
