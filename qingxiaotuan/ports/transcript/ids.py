"""Agent/transcript id types and helpers (对齐上游 model/ids 的类型)."""

from __future__ import annotations

import math
from typing import NewType

TurnId = NewType("TurnId", str)
StepId = NewType("StepId", str)
FrameId = NewType("FrameId", str)
MarkerId = NewType("MarkerId", str)
TaskRefId = NewType("TaskRefId", str)
TaskId = NewType("TaskId", str)
AgentId = NewType("AgentId", str)
InteractionId = NewType("InteractionId", str)
AttachmentId = NewType("AttachmentId", str)
TodoId = NewType("TodoId", str)
PromptId = NewType("PromptId", str)
ItemId = NewType("ItemId", str)

# Convenience aliases so callers can pass plain strings.
TurnId = str  # type: ignore[assignment]
StepId = str  # type: ignore[assignment]
FrameId = str  # type: ignore[assignment]
MarkerId = str  # type: ignore[assignment]
TaskRefId = str  # type: ignore[assignment]
TaskId = str  # type: ignore[assignment]
AgentId = str  # type: ignore[assignment]
InteractionId = str  # type: ignore[assignment]
AttachmentId = str  # type: ignore[assignment]
TodoId = str  # type: ignore[assignment]
PromptId = str  # type: ignore[assignment]
ItemId = str  # type: ignore[assignment]


def turn_id(ordinal: int) -> TurnId:
    return f"t{ordinal}"


def step_id(turn: TurnId, ordinal: int) -> StepId:
    return f"{turn}.{ordinal}"


def frame_id(step: StepId, ordinal: int) -> FrameId:
    return f"{step}.f{ordinal}"


def compare_turn_ids(a: TurnId, b: TurnId) -> int:
    return turn_ordinal(a) - turn_ordinal(b)


def turn_ordinal(id: TurnId) -> int:
    s = id[1:] if id else ""
    try:
        n = float(s)
    except (ValueError, TypeError):
        return 0
    if not math.isfinite(n):
        return 0
    return int(n)
