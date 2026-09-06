"""Transcript store / builder (对齐上游 store/agentTranscript + store/transcriptStore 的数据格式)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .apply import AgentState, EMPTY_AGENT_STATE, apply_operation
from .ids import (
    AgentId,
    AttachmentId,
    InteractionId,
    TaskId,
    TodoId,
    TurnId,
)
from .model import (
    TranscriptAttachment,
    TranscriptInteraction,
    TranscriptItem,
    TranscriptTask,
    TranscriptTodo,
    TranscriptTurn,
)
from .operation import AgentTranscriptSnapshot, TranscriptOperation


class Disposable:
    def __init__(self, dispose: Callable[[], None]):
        self._dispose = dispose

    def dispose(self) -> None:
        self._dispose()


TranscriptListener = Callable[[Any], None]


class AgentTranscript:
    def __init__(self, agent_id: AgentId):
        self._state: AgentState = EMPTY_AGENT_STATE
        self._listeners: set[TranscriptListener] = set()
        self.agent_id = agent_id

    def receive(self, ops: list) -> dict:
        return self.apply(ops)

    def apply(self, ops: list) -> dict:
        accepted: list = []
        gap = None
        state = self._state
        for op in ops:
            result = apply_operation(state, op)
            if result.gap is not None:
                gap = {"target": op.target, **result.gap} if hasattr(op, "target") else result.gap
                continue
            if not result.changed:
                continue
            state = result.state
            accepted.append(op)
        self._state = state
        if accepted:
            event = {"agentId": self.agent_id, "ops": accepted}
            for listener in list(self._listeners):
                listener(event)
        return {"accepted": accepted, "gap": gap}

    def on_change(self, listener: TranscriptListener) -> Disposable:
        self._listeners.add(listener)
        return Disposable(lambda: self._listeners.discard(listener))

    def get_items(self) -> list:
        return list(self._state.items)

    def get_turn(self, turn_id: TurnId) -> Optional[TranscriptTurn]:
        item = next((e for e in self._state.items if e.kind == "turn" and e.turn_id == turn_id), None)
        return item if isinstance(item, TranscriptTurn) else None

    def get_tasks(self) -> dict:
        return dict(self._state.tasks)

    def get_task(self, task_id: TaskId):
        return self._state.tasks.get(task_id)

    def get_interactions(self) -> dict:
        return dict(self._state.interactions)

    def get_interaction(self, interaction_id: InteractionId):
        return self._state.interactions.get(interaction_id)

    def get_attachments(self) -> dict:
        return dict(self._state.attachments)

    def get_attachment(self, attachment_id: AttachmentId):
        return self._state.attachments.get(attachment_id)

    def get_todos(self) -> dict:
        return dict(self._state.todos)

    def get_todo(self, todo_id: TodoId):
        return self._state.todos.get(todo_id)

    def get_prompts(self) -> dict:
        return dict(self._state.prompts)

    def get_prompt(self, prompt_id: str):
        return self._state.prompts.get(prompt_id)

    def get_meta(self):
        return self._state.meta

    def list_pending_interactions(self) -> list:
        return list(self._state.pending_interactions)

    @property
    def has_more_older(self) -> bool:
        return self._state.has_more_older

    def snapshot(self, window: Optional[dict] = None) -> AgentTranscriptSnapshot:
        items = list(self._state.items)
        has_more_older = self._state.has_more_older
        if window is not None:
            turn_count = sum(1 for e in items if e.kind == "turn")
            if turn_count > window["tailTurns"]:
                skip = turn_count - window["tailTurns"]
                kept: list = []
                seen = 0
                for entry in items:
                    if entry.kind == "turn":
                        seen += 1
                        if seen <= skip:
                            continue
                        kept.append(entry)
                    elif seen > skip:
                        kept.append(entry)
                items = kept
                has_more_older = True
        return AgentTranscriptSnapshot(
            items=list(items),
            tasks=list(self._state.tasks.values()),
            interactions=list(self._state.interactions.values()),
            attachments=list(self._state.attachments.values()),
            todos=list(self._state.todos.values()),
            prompts=list(self._state.prompts.values()),
            meta=self._state.meta,
            has_more_older=has_more_older,
        )


# --------------------------------------------------------------------------- #


@dataclass
class AgentDescriptor:
    agent_id: AgentId
    type: Optional[str] = None  # 'main' | 'sub' | 'independent'
    parent_agent_id: Optional[AgentId] = None
    label: Optional[str] = None
    created_at: Optional[str] = None
    disposed_at: Optional[str] = None


RosterListener = Callable[[list], None]


class TranscriptStore:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._agents: dict = {}
        self._descriptors: dict = {}
        self._roster_listeners: set[RosterListener] = set()

    def ensure_agent(self, agent_id: AgentId, descriptor: Optional[AgentDescriptor] = None) -> AgentTranscript:
        transcript = self._agents.get(agent_id)
        if transcript is None:
            transcript = AgentTranscript(agent_id)
            self._agents[agent_id] = transcript
        if descriptor is not None and self._descriptors.get(agent_id) is not descriptor:
            self._descriptors[agent_id] = descriptor
            self._emit_roster()
        return transcript

    def get_agent(self, agent_id: AgentId) -> Optional[AgentTranscript]:
        return self._agents.get(agent_id)

    def remove_agent(self, agent_id: AgentId) -> bool:
        removed = self._agents.pop(agent_id, None) is not None
        if self._descriptors.pop(agent_id, None) is not None or removed:
            self._emit_roster()
        return removed

    def describe_agent(self, descriptor: AgentDescriptor) -> None:
        if self._descriptors.get(descriptor.agent_id) is not descriptor:
            self._descriptors[descriptor.agent_id] = descriptor
            self._emit_roster()

    def mark_disposed(self, agent_id: AgentId, disposed_at: str) -> None:
        descriptor = self._descriptors.get(agent_id)
        if descriptor is None or descriptor.disposed_at is not None:
            return
        self.describe_agent(AgentDescriptor(**{**_descriptor_fields(descriptor), "disposed_at": disposed_at}))

    def agents(self) -> list:
        return list(self._descriptors.values())

    def on_roster_change(self, listener: RosterListener) -> Disposable:
        self._roster_listeners.add(listener)
        return Disposable(lambda: self._roster_listeners.discard(listener))

    def _emit_roster(self) -> None:
        agents = self.agents()
        for listener in list(self._roster_listeners):
            listener(agents)


def _descriptor_fields(descriptor: AgentDescriptor) -> dict:
    return {
        "agent_id": descriptor.agent_id,
        "type": descriptor.type,
        "parent_agent_id": descriptor.parent_agent_id,
        "label": descriptor.label,
        "created_at": descriptor.created_at,
        "disposed_at": descriptor.disposed_at,
    }
