"""Transcript wire events (对齐上游 contract/events 的线协议)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .exceptions import TranscriptDecodeError
from .operation import (
    AgentTranscriptSnapshot,
    operation_from_dict,
    operation_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)

TRANSCRIPT_EVENT_TYPES = ("transcript.reset", "transcript.ops")
TranscriptEventType = str


@dataclass
class TranscriptResetEvent:
    type: str = "transcript.reset"
    agent_id: str = ""
    snapshot: Any = None  # AgentTranscriptSnapshot
    has_more_older: bool = False
    seq: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"type": "transcript.reset", "agent_id": self.agent_id, "snapshot": snapshot_to_dict(self.snapshot), "has_more_older": self.has_more_older}
        if self.seq is not None:
            d["seq"] = self.seq
        return d


@dataclass
class TranscriptOpsEvent:
    type: str = "transcript.ops"
    agent_id: str = ""
    ops: list = field(default_factory=list)
    seq: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"type": "transcript.ops", "agent_id": self.agent_id, "ops": [operation_to_dict(op) for op in self.ops]}
        if self.seq is not None:
            d["seq"] = self.seq
        return d


TranscriptEvent = Any


def event_to_dict(event: Any) -> dict:
    return event.to_dict()


def event_from_dict(d: Any) -> Any:
    if not isinstance(d, dict):
        raise TranscriptDecodeError("event must be an object")
    t = d.get("type")
    if t == "transcript.reset":
        return TranscriptResetEvent(
            agent_id=d["agent_id"],
            snapshot=snapshot_from_dict(d["snapshot"]),
            has_more_older=bool(d.get("has_more_older", False)),
            seq=d.get("seq"),
        )
    if t == "transcript.ops":
        return TranscriptOpsEvent(
            agent_id=d["agent_id"],
            ops=[operation_from_dict(op) for op in d.get("ops", [])],
            seq=d.get("seq"),
        )
    raise TranscriptDecodeError(f"unknown event type: {t!r}")
