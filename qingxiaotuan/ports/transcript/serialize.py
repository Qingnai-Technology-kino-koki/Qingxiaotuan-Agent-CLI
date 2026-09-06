"""JSON (de)serialization for transcripts and operations (对齐上游 contract/schema 的数据格式).

The TypeScript source validates wire data with zod. We reimplement the same
round-trip behavior with the standard library only: model dataclasses already
expose camelCase dicts, so serialization is plain ``json`` with structural
validation that raises :class:`TranscriptDecodeError` on malformed input.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .exceptions import TranscriptDecodeError
from .events import event_from_dict, event_to_dict
from .operation import (
    AgentTranscriptSnapshot,
    operation_from_dict,
    operation_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)

_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def is_plain_agent_id(agent_id: str) -> bool:
    return bool(_AGENT_ID_PATTERN.match(agent_id)) and agent_id not in (".", "..")


# Snapshot ------------------------------------------------------------------- #


def dump_snapshot(snapshot: AgentTranscriptSnapshot) -> str:
    return json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False)


def load_snapshot(data: str) -> AgentTranscriptSnapshot:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise TranscriptDecodeError(f"invalid JSON: {exc}") from exc
    return snapshot_from_dict(raw)


def serialize_snapshot(snapshot: AgentTranscriptSnapshot) -> dict:
    return snapshot_to_dict(snapshot)


def deserialize_snapshot(data: dict) -> AgentTranscriptSnapshot:
    return snapshot_from_dict(data)


# Operations ----------------------------------------------------------------- #


def dump_operation(op: Any) -> str:
    return json.dumps(operation_to_dict(op), ensure_ascii=False)


def load_operation(data: str) -> Any:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise TranscriptDecodeError(f"invalid JSON: {exc}") from exc
    return operation_from_dict(raw)


def serialize_operation(op: Any) -> dict:
    return operation_to_dict(op)


def deserialize_operation(data: dict) -> Any:
    return operation_from_dict(data)


# Ops batch ------------------------------------------------------------------ #


def dump_ops_batch(agent_id: str, ops: list) -> str:
    return json.dumps({"agentId": agent_id, "ops": [operation_to_dict(op) for op in ops]}, ensure_ascii=False)


def load_ops_batch(data: str) -> dict:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise TranscriptDecodeError(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise TranscriptDecodeError("ops batch must be an object")
    return {"agentId": raw["agentId"], "ops": [operation_from_dict(op) for op in raw.get("ops", [])]}


# Events --------------------------------------------------------------------- #


def dump_event(event: Any) -> str:
    return json.dumps(event_to_dict(event), ensure_ascii=False)


def load_event(data: str) -> Any:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise TranscriptDecodeError(f"invalid JSON: {exc}") from exc
    return event_from_dict(raw)
