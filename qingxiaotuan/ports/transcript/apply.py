"""Apply transcript operations to produce new agent state (对齐上游 ops/apply 的语义)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .ids import turn_ordinal
from .model import (
    AgentStatusMeta,
    GoalMeta,
    ModesMeta,
    TranscriptAttachment,
    TranscriptFrame,
    TranscriptInteraction,
    TranscriptItem,
    TranscriptMarker,
    TranscriptMeta,
    TranscriptStep,
    TranscriptTask,
    TranscriptTaskRef,
    TranscriptTodo,
    TranscriptTurn,
)
from .operation import AppendTarget, TranscriptOperation


@dataclass
class AgentState:
    items: list = field(default_factory=list)  # list[TranscriptItem]
    tasks: dict = field(default_factory=dict)
    interactions: dict = field(default_factory=dict)
    attachments: dict = field(default_factory=dict)
    todos: dict = field(default_factory=dict)
    prompts: dict = field(default_factory=dict)
    meta: TranscriptMeta = field(default_factory=TranscriptMeta)
    pending_interactions: set = field(default_factory=set)
    has_more_older: bool = False


EMPTY_AGENT_STATE = AgentState(
    items=[],
    tasks={},
    interactions={},
    attachments={},
    todos={},
    prompts={},
    meta=TranscriptMeta(),
    pending_interactions=set(),
    has_more_older=False,
)


@dataclass
class ApplyResult:
    state: AgentState
    changed: bool
    gap: Optional[dict] = None  # {expected, got}


def apply_operation(state: AgentState, op: TranscriptOperation) -> ApplyResult:
    name = op.op
    if name == "reset":
        return _apply_reset(state, op)
    if name == "turn.upsert":
        return _apply_turn_upsert(state, op.turn)
    if name == "step.upsert":
        return _apply_step_upsert(state, op.turn_id, op.step)
    if name == "frame.upsert":
        return _apply_frame_upsert(state, op)
    if name == "append":
        return _apply_append(state, op)
    if name == "marker.upsert":
        return _apply_item_upsert(state, op.item, op.item.marker_id, op.before_turn)
    if name == "taskref.upsert":
        return _apply_item_upsert(state, op.item, op.item.ref_id, op.before_turn)
    if name == "task.upsert":
        return _apply_task_upsert(state, op.task)
    if name == "interaction.upsert":
        return _apply_interaction_upsert(state, op.interaction)
    if name == "attachment.upsert":
        return _apply_attachment_upsert(state, op.attachment)
    if name == "todo.upsert":
        return _apply_todo_upsert(state, op.todo)
    if name == "prompt.upsert":
        return _apply_prompt_upsert(state, op.prompt)
    if name == "meta.merge":
        return _apply_meta_merge(state, op.meta)
    if name == "items.remove":
        return _apply_items_remove(state, op.ids)
    return ApplyResult(state=state, changed=False)


# --------------------------------------------------------------------------- #


def _apply_reset(state: AgentState, op: Any) -> ApplyResult:
    snap = op.snapshot
    pending = {i.interaction_id for i in snap.interactions if i.state == "pending"}
    return ApplyResult(
        state=AgentState(
            items=list(snap.items),
            tasks={t.task_id: t for t in snap.tasks},
            interactions={i.interaction_id: i for i in snap.interactions},
            attachments={a.attachment_id: a for a in snap.attachments},
            todos={t.todo_id: t for t in snap.todos},
            prompts={p.prompt_id: p for p in snap.prompts},
            meta=snap.meta,
            pending_interactions=pending,
            has_more_older=bool(snap.has_more_older),
        ),
        changed=True,
    )


def _turn_header_to_turn(header: TranscriptTurn, steps: list) -> TranscriptTurn:
    return TranscriptTurn(
        kind="turn",
        turn_id=header.turn_id,
        ordinal=header.ordinal,
        state=header.state,
        origin=header.origin,
        prompt=header.prompt,
        attachment_ids=header.attachment_ids,
        steps=list(steps),
        started_at=header.started_at,
        ended_at=header.ended_at,
        usage=header.usage,
        duration_ms=header.duration_ms,
        error=header.error,
    )


def _skeleton_turn(turn_id: str) -> TranscriptTurn:
    return TranscriptTurn(
        kind="turn",
        turn_id=turn_id,
        ordinal=turn_ordinal(turn_id),
        state="running",
        origin={"kind": "other"},
        steps=[],
    )


def _skeleton_step(step_id: str, turn_id: str) -> TranscriptStep:
    ordinal = int(step_id[len(turn_id) + 1 :]) if step_id.startswith(turn_id + ".") else 0
    return TranscriptStep(kind="step", step_id=step_id, turn_id=turn_id, ordinal=ordinal or 0, state="running", frames=[])


def _get_turn(state: AgentState, turn_id: str) -> Optional[TranscriptTurn]:
    for entry in state.items:
        if entry.kind == "turn" and entry.turn_id == turn_id:
            return entry
    return None


def _insert_turn(items: list, turn: TranscriptTurn) -> list:
    nxt = list(items)
    at = len(nxt)
    for i, entry in enumerate(nxt):
        if entry.kind == "turn" and entry.ordinal > turn.ordinal:
            at = i
            break
    nxt.insert(at, turn)
    return nxt


def _replace_turn(items: list, turn_id: str, fn) -> list:
    return [fn(entry) if (entry.kind == "turn" and entry.turn_id == turn_id) else entry for entry in items]


def _apply_turn_upsert(state: AgentState, header: TranscriptTurn) -> ApplyResult:
    existing = _get_turn(state, header.turn_id)
    if existing is not None:
        if _turn_equals(existing, header):
            return ApplyResult(state=state, changed=False)
        return ApplyResult(
            state=AgentState(
                **{**_state_fields(state), "items": _replace_turn(state.items, header.turn_id, lambda t: _turn_header_to_turn(header, t.steps))}
            ),
            changed=True,
        )
    return ApplyResult(
        state=AgentState(**{**_state_fields(state), "items": _insert_turn(state.items, _turn_header_to_turn(header, []))}),
        changed=True,
    )


def _turn_equals(turn: TranscriptTurn, header: TranscriptTurn) -> bool:
    return (
        turn.ordinal == header.ordinal
        and turn.state == header.state
        and turn.prompt == header.prompt
        and turn.attachment_ids == header.attachment_ids
        and turn.started_at == header.started_at
        and turn.ended_at == header.ended_at
        and (turn.origin or {}).get("kind") == (header.origin or {}).get("kind")
        and (turn.origin or {}).get("payload") == (header.origin or {}).get("payload")
        and turn.usage == header.usage
        and turn.duration_ms == header.duration_ms
        and turn.error == header.error
    )


def _apply_step_upsert(state: AgentState, turn_id: str, header: TranscriptStep) -> ApplyResult:
    turn = _get_turn(state, turn_id) or _skeleton_turn(turn_id)
    step_index = next((i for i, s in enumerate(turn.steps) if s.step_id == header.step_id), -1)
    if step_index >= 0:
        current = turn.steps[step_index]
        if _step_equals(current, header):
            return ApplyResult(state=state, changed=False)
        new_step = TranscriptStep(
            kind="step", step_id=header.step_id, turn_id=header.turn_id, ordinal=header.ordinal,
            state=header.state, frames=current.frames, started_at=header.started_at,
            ended_at=header.ended_at, usage=header.usage, finish_reason=header.finish_reason,
            timing=header.timing, retry=header.retry, end_reason=header.end_reason,
            end_message=header.end_message,
        )
        steps = [new_step if s.step_id == header.step_id else s for s in turn.steps]
    else:
        new_step = TranscriptStep(
            kind="step", step_id=header.step_id, turn_id=header.turn_id, ordinal=header.ordinal,
            state=header.state, frames=[], started_at=header.started_at, ended_at=header.ended_at,
            usage=header.usage, finish_reason=header.finish_reason, timing=header.timing,
            retry=header.retry, end_reason=header.end_reason, end_message=header.end_message,
        )
        steps = sorted([*turn.steps, new_step], key=lambda s: s.ordinal)
    next_turn = TranscriptTurn(**{**_turn_kwargs(turn), "steps": steps})
    items = _replace_turn(state.items, turn_id, lambda _: next_turn) if _get_turn(state, turn_id) else _insert_turn(state.items, next_turn)
    return ApplyResult(state=AgentState(**{**_state_fields(state), "items": items}), changed=True)


def _step_equals(step: TranscriptStep, header: TranscriptStep) -> bool:
    return (
        step.ordinal == header.ordinal
        and step.state == header.state
        and step.started_at == header.started_at
        and step.ended_at == header.ended_at
        and step.usage == header.usage
        and step.finish_reason == header.finish_reason
        and step.timing == header.timing
        and step.retry == header.retry
        and step.end_reason == header.end_reason
        and step.end_message == header.end_message
    )


def _apply_frame_upsert(state: AgentState, op: Any) -> ApplyResult:
    turn = _get_turn(state, op.turn_id) or _skeleton_turn(op.turn_id)
    step = next((s for s in turn.steps if s.step_id == op.step_id), None) or _skeleton_step(op.step_id, op.turn_id)
    existing = next((i for i, f in enumerate(step.frames) if f.frame_id == op.frame.frame_id), -1)
    if existing >= 0:
        current = step.frames[existing]
        if _frame_equals(current, op.frame):
            return ApplyResult(state=state, changed=False)
        frames = [op.frame if f.frame_id == op.frame.frame_id else f for f in step.frames]
    else:
        frames = [*step.frames, op.frame]
    next_step = TranscriptStep(**{**_step_kwargs(step), "frames": list(frames)})
    steps = (
        [next_step if s.step_id == op.step_id else s for s in turn.steps]
        if any(s.step_id == op.step_id for s in turn.steps)
        else sorted([*turn.steps, next_step], key=lambda s: s.ordinal)
    )
    next_turn = TranscriptTurn(**{**_turn_kwargs(turn), "steps": steps})
    items = _get_turn(state, op.turn_id) and _replace_turn(state.items, op.turn_id, lambda _: next_turn) or _insert_turn(state.items, next_turn)
    return ApplyResult(state=AgentState(**{**_state_fields(state), "items": items}), changed=True)


def _frame_equals(a: TranscriptFrame, b: TranscriptFrame) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == "text" and b.kind == "text":
        return a.text == b.text and a.role == b.role and a.attachment_ids == b.attachment_ids and a.task_id == b.task_id
    if a.kind == "thinking" and b.kind == "thinking":
        return a.text == b.text
    if a.kind == "tool" and b.kind == "tool":
        return (
            a.state == b.state
            and a.tool_call_id == b.tool_call_id
            and a.name == b.name
            and a.view == b.view
            and a.input == b.input
            and a.output == b.output
            and a.display == b.display
            and a.error == b.error
            and a.input_text == b.input_text
            and a.progress == b.progress
            and a.task_id == b.task_id
            and a.approval_id == b.approval_id
            and a.todo_id == b.todo_id
            and a.agent_refs == b.agent_refs
        )
    if a.kind == "notice" and b.kind == "notice":
        return a.message == b.message and a.level == b.level and a.detail == b.detail
    return False


def _apply_append(state: AgentState, op: Any) -> ApplyResult:
    if op.target.type == "task":
        return _apply_task_append(state, op)
    turn_id, step_id, frame_id = op.target.turn_id, op.target.step_id, op.target.frame_id
    turn = _get_turn(state, turn_id)
    if turn is None:
        return ApplyResult(state=state, changed=False, gap={"expected": 0, "got": op.offset})
    step = next((s for s in turn.steps if s.step_id == step_id), None)
    frame = next((f for f in step.frames if f.frame_id == frame_id), None) if step is not None else None
    if step is None or frame is None or frame.kind not in ("text", "thinking"):
        return ApplyResult(state=state, changed=False, gap={"expected": 0, "got": op.offset})
    merged = append_at_offset(frame.text, op.offset, op.text)
    if merged.get("gap") is not None:
        return ApplyResult(state=state, changed=False, gap=merged["gap"])
    if not merged["changed"]:
        return ApplyResult(state=state, changed=False)
    next_frame = type(frame)(**{**_frame_kwargs(frame), "text": merged["text"]})
    next_step = TranscriptStep(**{**_step_kwargs(step), "frames": [next_frame if f.frame_id == frame_id else f for f in step.frames]})
    next_turn = TranscriptTurn(**{**_turn_kwargs(turn), "steps": [next_step if s.step_id == step_id else s for s in turn.steps]})
    return ApplyResult(
        state=AgentState(**{**_state_fields(state), "items": _replace_turn(state.items, turn_id, lambda _: next_turn)}),
        changed=True,
    )


def _apply_task_append(state: AgentState, op: Any) -> ApplyResult:
    task_id = op.target.task_id
    task = state.tasks.get(task_id)
    current = task.output_tail if task else ""
    merged = append_at_offset(current, op.offset, op.text)
    if merged.get("gap") is not None:
        return ApplyResult(state=state, changed=False, gap=merged["gap"])
    if not merged["changed"]:
        return ApplyResult(state=state, changed=False)
    next_task = task or TranscriptTask(task_id=task_id, kind="other", state="running", detached=False, output_tail="")
    next_task = TranscriptTask(**{**_task_kwargs(next_task), "output_tail": merged["text"]})
    tasks = dict(state.tasks)
    tasks[task_id] = next_task
    return ApplyResult(state=AgentState(**{**_state_fields(state), "tasks": tasks}), changed=True)


def append_at_offset(local: str, offset: int, chunk: str) -> dict:
    if offset > len(local):
        return {"text": local, "changed": False, "gap": {"expected": len(local), "got": offset}}
    if local[offset : offset + len(chunk)] == chunk:
        return {"text": local, "changed": False}
    overlap = len(local) - offset
    if local[offset:] != chunk[:overlap]:
        return {"text": local, "changed": False, "gap": {"expected": len(local), "got": offset}}
    novel = chunk[overlap:] if overlap > 0 else chunk
    if len(novel) == 0:
        return {"text": local, "changed": False}
    return {"text": local[:offset] + chunk, "changed": True}


def _apply_item_upsert(state: AgentState, item: TranscriptItem, id: str, before_turn: Optional[int] = None) -> ApplyResult:
    exists = any(_item_id_of(e) == id for e in state.items)
    if exists:
        changed = False
        items = []
        for e in state.items:
            if _item_id_of(e) != id:
                items.append(e)
            elif e is item:
                items.append(e)
            else:
                changed = True
                items.append(item)
        if not changed:
            return ApplyResult(state=state, changed=False)
        return ApplyResult(state=AgentState(**{**_state_fields(state), "items": items}), changed=True)
    if before_turn is not None:
        items = list(state.items)
        at = len(items)
        for i, e in enumerate(items):
            if e.kind == "turn" and e.ordinal >= before_turn:
                at = i
                break
        items.insert(at, item)
        return ApplyResult(state=AgentState(**{**_state_fields(state), "items": items}), changed=True)
    return ApplyResult(state=AgentState(**{**_state_fields(state), "items": [*state.items, item]}), changed=True)


def _item_id_of(item: TranscriptItem) -> str:
    if item.kind == "turn":
        return item.turn_id
    if item.kind == "marker":
        return item.marker_id
    return item.ref_id


def _apply_items_remove(state: AgentState, ids: list) -> ApplyResult:
    drop = set(ids)
    removed_turns = [e for e in state.items if e.kind == "turn" and e.turn_id in drop]
    items = [e for e in state.items if _item_id_of(e) not in drop]
    if len(items) == len(state.items):
        return ApplyResult(state=state, changed=False)
    pending = state.pending_interactions
    interactions = state.interactions
    if removed_turns:
        anchored_tool_call_ids = set()
        for turn in removed_turns:
            for step in turn.steps:
                for frame in step.frames:
                    if frame.kind == "tool":
                        anchored_tool_call_ids.add(frame.tool_call_id)
        dead = set()
        next_pending = set(pending)
        for interaction in interactions.values():
            if interaction.tool_call_id is not None and interaction.tool_call_id in anchored_tool_call_ids:
                dead.add(interaction.interaction_id)
                next_pending.discard(interaction.interaction_id)
        if dead:
            interactions = {k: v for k, v in interactions.items() if k not in dead}
        pending = next_pending
    return ApplyResult(
        state=AgentState(**{**_state_fields(state), "items": items, "interactions": interactions, "pending_interactions": pending}),
        changed=True,
    )


def _apply_task_upsert(state: AgentState, task: TranscriptTask) -> ApplyResult:
    current = state.tasks.get(task.task_id)
    if current is not None and _task_equals(current, task):
        return ApplyResult(state=state, changed=False)
    tasks = dict(state.tasks)
    tasks[task.task_id] = task
    return ApplyResult(state=AgentState(**{**_state_fields(state), "tasks": tasks}), changed=True)


def _apply_interaction_upsert(state: AgentState, interaction: TranscriptInteraction) -> ApplyResult:
    current = state.interactions.get(interaction.interaction_id)
    if current is not None and _interaction_equals(current, interaction):
        return ApplyResult(state=state, changed=False)
    interactions = dict(state.interactions)
    interactions[interaction.interaction_id] = interaction
    pending = state.pending_interactions
    if interaction.state == "pending":
        if interaction.interaction_id not in pending:
            pending = {*pending, interaction.interaction_id}
    elif interaction.interaction_id in pending:
        pending = {i for i in pending if i != interaction.interaction_id}
    return ApplyResult(state=AgentState(**{**_state_fields(state), "interactions": interactions, "pending_interactions": pending}), changed=True)


def _apply_attachment_upsert(state: AgentState, attachment: TranscriptAttachment) -> ApplyResult:
    current = state.attachments.get(attachment.attachment_id)
    if current is not None and _attachment_equals(current, attachment):
        return ApplyResult(state=state, changed=False)
    attachments = dict(state.attachments)
    attachments[attachment.attachment_id] = attachment
    return ApplyResult(state=AgentState(**{**_state_fields(state), "attachments": attachments}), changed=True)


def _apply_todo_upsert(state: AgentState, todo: TranscriptTodo) -> ApplyResult:
    current = state.todos.get(todo.todo_id)
    if current is not None and _todo_equals(current, todo):
        return ApplyResult(state=state, changed=False)
    todos = dict(state.todos)
    todos[todo.todo_id] = todo
    return ApplyResult(state=AgentState(**{**_state_fields(state), "todos": todos}), changed=True)


def _apply_prompt_upsert(state: AgentState, prompt: Any) -> ApplyResult:
    current = state.prompts.get(prompt.prompt_id)
    if current is not None and _prompt_equals(current, prompt):
        return ApplyResult(state=state, changed=False)
    prompts = dict(state.prompts)
    prompts[prompt.prompt_id] = prompt
    return ApplyResult(state=AgentState(**{**_state_fields(state), "prompts": prompts}), changed=True)


def _apply_meta_merge(state: AgentState, meta: Any) -> ApplyResult:
    if not isinstance(meta, dict):
        return ApplyResult(state=state, changed=False)
    # goal: explicit null clears; present value wins; absent keeps base.
    if "goal" in meta:
        goal = None if meta["goal"] is None else _coalesce_goal(meta["goal"], state.meta.goal)
    else:
        goal = state.meta.goal
    # modes
    if "modes" in meta:
        raw = meta["modes"] or {}
        modes = ModesMeta(
            plan=_merge_mode(raw, "plan", state.meta.modes.plan if state.meta.modes else None),
            swarm=_merge_mode(raw, "swarm", state.meta.modes.swarm if state.meta.modes else None),
            tower=_merge_mode(raw, "tower", state.meta.modes.tower if state.meta.modes else None),
        )
        if modes.plan is None and modes.swarm is None and modes.tower is None:
            modes = None
    else:
        modes = state.meta.modes
    # agent
    if "agent" in meta and meta["agent"] is not None:
        base_d = state.meta.agent.to_dict() if state.meta.agent else {}
        merged = {**base_d, **meta["agent"]}
        agent = AgentStatusMeta.from_dict(merged)
    else:
        agent = state.meta.agent
    activity = meta.get("activity", state.meta.activity)
    next_meta = TranscriptMeta(goal=goal, activity=activity, modes=modes, agent=agent)
    if (
        next_meta.goal == state.meta.goal
        and next_meta.activity == state.meta.activity
        and next_meta.modes == state.meta.modes
        and next_meta.agent == state.meta.agent
    ):
        return ApplyResult(state=state, changed=False)
    return ApplyResult(state=AgentState(**{**_state_fields(state), "meta": next_meta}), changed=True)


def _merge_mode(raw: dict, key: str, base: Any) -> Any:
    if key in raw:
        v = raw[key]
        return None if v is None else v
    return base


def _coalesce_goal(incoming: Any, base: Optional[GoalMeta]) -> Optional[GoalMeta]:
    if incoming is None:
        return None
    if isinstance(incoming, GoalMeta):
        return incoming
    return GoalMeta.from_dict(incoming)


# Equality helpers ----------------------------------------------------------- #


def _interaction_equals(a: TranscriptInteraction, b: TranscriptInteraction) -> bool:
    return (
        a.interaction_kind == b.interaction_kind
        and a.tool_call_id == b.tool_call_id
        and a.state == b.state
        and a.request == b.request
        and a.response == b.response
    )


def _attachment_equals(a: TranscriptAttachment, b: TranscriptAttachment) -> bool:
    return a.media_type == b.media_type and a.name == b.name and a.size == b.size and a.source == b.source and a.placeholder == b.placeholder


def _todo_equals(a: TranscriptTodo, b: TranscriptTodo) -> bool:
    return a.items == b.items and a.updated_at == b.updated_at


def _prompt_equals(a: Any, b: Any) -> bool:
    return (
        a.status == b.status
        and a.user_message_id == b.user_message_id
        and a.content == b.content
        and a.created_at == b.created_at
        and a.finished_at == b.finished_at
        and a.steered_at == b.steered_at
    )


def _task_equals(a: TranscriptTask, b: TranscriptTask) -> bool:
    return (
        a.kind == b.kind
        and a.state == b.state
        and a.detached == b.detached
        and a.description == b.description
        and a.agent_id == b.agent_id
        and a.output_tail == b.output_tail
        and a.started_at == b.started_at
        and a.ended_at == b.ended_at
        and a.result_summary == b.result_summary
        and a.error == b.error
        and a.state_reason == b.state_reason
        and a.usage == b.usage
    )


# dict-kw helpers ------------------------------------------------------------- #


def _state_fields(state: AgentState) -> dict:
    return {
        "items": state.items,
        "tasks": state.tasks,
        "interactions": state.interactions,
        "attachments": state.attachments,
        "todos": state.todos,
        "prompts": state.prompts,
        "meta": state.meta,
        "pending_interactions": state.pending_interactions,
        "has_more_older": state.has_more_older,
    }


def _turn_kwargs(turn: TranscriptTurn) -> dict:
    return {
        "kind": "turn",
        "turn_id": turn.turn_id,
        "ordinal": turn.ordinal,
        "state": turn.state,
        "origin": turn.origin,
        "prompt": turn.prompt,
        "attachment_ids": turn.attachment_ids,
        "steps": turn.steps,
        "started_at": turn.started_at,
        "ended_at": turn.ended_at,
        "usage": turn.usage,
        "duration_ms": turn.duration_ms,
        "error": turn.error,
    }


def _step_kwargs(step: TranscriptStep) -> dict:
    return {
        "kind": "step",
        "step_id": step.step_id,
        "turn_id": step.turn_id,
        "ordinal": step.ordinal,
        "state": step.state,
        "frames": step.frames,
        "started_at": step.started_at,
        "ended_at": step.ended_at,
        "usage": step.usage,
        "finish_reason": step.finish_reason,
        "timing": step.timing,
        "retry": step.retry,
        "end_reason": step.end_reason,
        "end_message": step.end_message,
    }


def _frame_kwargs(frame: Any) -> dict:
    return {k: v for k, v in frame.__dict__.items()}


def _task_kwargs(task: TranscriptTask) -> dict:
    return {
        "task_id": task.task_id,
        "kind": task.kind,
        "state": task.state,
        "detached": task.detached,
        "description": task.description,
        "agent_id": task.agent_id,
        "output_tail": task.output_tail,
        "started_at": task.started_at,
        "ended_at": task.ended_at,
        "result_summary": task.result_summary,
        "error": task.error,
        "state_reason": task.state_reason,
        "usage": task.usage,
        "model": task.model,
        "thinking_effort": task.thinking_effort,
    }
