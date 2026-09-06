"""Transcript granularity grading and op filtering (对齐上游 granularity 分级语义)."""

from __future__ import annotations

from typing import Optional

from .operation import AgentTranscriptSnapshot, TranscriptOperation

TranscriptGrade = str  # 'off' | 'turn' | 'block' | 'delta'

GRADE_RANK: dict[str, int] = {"off": 0, "turn": 1, "block": 2, "delta": 3}

TranscriptGradeSpec = dict  # Record<agentId, grade | undefined>


def grade_for(spec: Optional[TranscriptGradeSpec], agent_id: str) -> TranscriptGrade:
    if not spec:
        return "off"
    return spec.get(agent_id, spec.get("*", "off"))


def needs_reset_on_transition(prev: TranscriptGrade, nxt: TranscriptGrade) -> bool:
    return GRADE_RANK[nxt] > GRADE_RANK[prev]


def detach_grades(spec: Optional[TranscriptGradeSpec], agent_ids: list) -> Optional[TranscriptGradeSpec]:
    if spec is None:
        return None
    nxt = dict(spec)
    for agent_id in agent_ids:
        if agent_id == "*":
            nxt.pop("*", None)
        else:
            nxt[agent_id] = "off"
    return nxt if any(g not in (None, "off") for g in nxt.values()) else None


# filterOps.ts ---------------------------------------------------------------- #


def filter_ops_for_grade(grade: TranscriptGrade, ops: list) -> list:
    rank = GRADE_RANK[grade]
    if rank == 0:
        return []
    return [op for op in ops if _admits(grade, op)]


def _admits(grade: TranscriptGrade, op: TranscriptOperation) -> bool:
    name = op.op
    if name == "append":
        return GRADE_RANK[grade] >= GRADE_RANK["delta"]
    if name in ("step.upsert", "frame.upsert"):
        return GRADE_RANK[grade] >= GRADE_RANK["block"]
    return True


def is_append_only(ops: list) -> bool:
    return len(ops) > 0 and all(op.op == "append" for op in ops)


def redact_snapshot_for_grade(grade: TranscriptGrade, snapshot: AgentTranscriptSnapshot) -> AgentTranscriptSnapshot:
    if GRADE_RANK[grade] >= GRADE_RANK["block"]:
        return snapshot
    return AgentTranscriptSnapshot(
        items=[_strip_steps(i) if i.kind == "turn" else i for i in snapshot.items],
        tasks=snapshot.tasks,
        interactions=snapshot.interactions,
        attachments=snapshot.attachments,
        todos=snapshot.todos,
        prompts=snapshot.prompts,
        meta=snapshot.meta,
        has_more_older=snapshot.has_more_older,
    )


def _strip_steps(item):
    from .model import TranscriptTurn

    return TranscriptTurn(**{**_turn_to_kwargs(item), "steps": []})


def _turn_to_kwargs(turn):
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
