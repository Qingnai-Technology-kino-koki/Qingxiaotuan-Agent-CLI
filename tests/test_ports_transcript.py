"""Behavioral tests for the transcript port (stdlib only)."""

from __future__ import annotations

import json

import pytest

from qingxiaotuan.ports.transcript import (
    AgentTranscript,
    AgentTranscriptSnapshot,
    AppendOp,
    AppendTargetFrame,
    FrameUpsertOp,
    TranscriptDecodeError,
    TranscriptStep,
    TranscriptStore,
    TranscriptTurn,
    TurnUpsertOp,
    dump_snapshot,
    fold_wire_record_facts,
    frame_id,
    group_messages_into_snapshot,
    is_plain_agent_id,
    load_snapshot,
    load_operation,
    paginate_turns,
    step_id,
    grade_for,
)
from qingxiaotuan.ports.transcript.model import TextFrame, TranscriptMarker


def _build_turn_ops() -> list:
    turn = TranscriptTurn(turn_id="t0", ordinal=0, state="running", origin={"kind": "user"}, prompt="hi", steps=[])
    step = TranscriptStep(step_id=step_id("t0", 1), turn_id="t0", ordinal=1, state="running", frames=[])
    frame = TextFrame(frame_id=frame_id(step_id("t0", 1), 1), role="assistant", text="")
    return [
        TurnUpsertOp(turn=turn),
        FrameUpsertOp(turn_id="t0", step_id=step.step_id, frame=frame),
    ]


def test_append_turn_serialize_deserialize_equals():
    tr = AgentTranscript("agent1")
    result = tr.apply(_build_turn_ops())
    assert len(result["accepted"]) == 2

    snap1 = tr.snapshot()
    assert isinstance(snap1, AgentTranscriptSnapshot)
    assert len(snap1.items) == 1

    payload = dump_snapshot(snap1)
    assert isinstance(payload, str)

    snap2 = load_snapshot(payload)
    # Feed the deserialized snapshot into a fresh transcript via reset.
    fresh = AgentTranscript("agent1")
    fresh.apply([_reset_op(snap2)])
    snap3 = fresh.snapshot()

    assert snap1 == snap3
    # Round-trip through dict form too.
    assert snap1 == load_snapshot(json.dumps(_snap_to_dict(snap1)))


def _reset_op(snapshot):
    from qingxiaotuan.ports.transcript import ResetOp

    return ResetOp(agent_id="agent1", snapshot=snapshot)


def _snap_to_dict(snapshot):
    from qingxiaotuan.ports.transcript.operation import snapshot_to_dict

    return snapshot_to_dict(snapshot)


def test_append_text_merges_and_detects_gap():
    tr = AgentTranscript("agent1")
    tr.apply(_build_turn_ops())

    step_id_ = step_id("t0", 1)
    frame_id_ = frame_id(step_id_, 1)
    target = AppendTargetFrame(turn_id="t0", step_id=step_id_, frame_id=frame_id_)

    # First chunk at offset 0.
    r1 = tr.apply([AppendOp(target=target, offset=0, text="Hello ")])
    assert r1["accepted"] and r1["gap"] is None
    # Contiguous chunk.
    r2 = tr.apply([AppendOp(target=target, offset=6, text="world")])
    assert r2["accepted"]
    assert tr.get_turn("t0").steps[0].frames[0].text == "Hello world"
    # Gap: offset beyond current length.
    r3 = tr.apply([AppendOp(target=target, offset=100, text="!")])
    assert r3["gap"] is not None
    assert r3["gap"]["expected"] == 11


def test_store_roster_and_agents():
    store = TranscriptStore("session-1")
    a = store.ensure_agent("main", None)
    assert isinstance(a, AgentTranscript)
    assert store.get_agent("main") is a
    store.describe_agent(__import__("qingxiaotuan.ports.transcript", fromlist=["AgentDescriptor"]).AgentDescriptor(agent_id="main", label="Main"))
    assert store.agents()[0].label == "Main"
    assert store.remove_agent("main") is True
    assert store.get_agent("main") is None


def test_malformed_input_raises():
    with pytest.raises(TranscriptDecodeError):
        load_snapshot("{not valid json")
    with pytest.raises(TranscriptDecodeError):
        load_snapshot(json.dumps({"items": "nope"}))
    with pytest.raises(TranscriptDecodeError):
        load_operation(json.dumps({"op": "bogus.op"}))


def test_grade_and_pagination_helpers():
    assert grade_for(None, "x") == "off"
    assert grade_for({"x": "block"}, "x") == "block"
    assert grade_for({"*": "turn"}, "y") == "turn"

    turns = [
        TranscriptTurn(turn_id="t0", ordinal=0, state="completed", origin={"kind": "user"}, steps=[]),
        TranscriptTurn(turn_id="t1", ordinal=1, state="completed", origin={"kind": "user"}, steps=[]),
        TranscriptTurn(turn_id="t2", ordinal=2, state="completed", origin={"kind": "user"}, steps=[]),
    ]
    page = paginate_turns(turns, {"pageSize": 2})
    assert len(page["items"]) == 2
    assert page["hasMore"] is True


def test_group_messages_into_snapshot_basic():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "plan a trip"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Sure, "}, {"type": "think", "think": "thinking..."}]},
        {"role": "assistant", "toolCalls": [{"id": "c1", "name": "shell", "arguments": '{"cmd": "ls"}'}]},
        {"role": "tool", "toolCallId": "c1", "isError": False, "content": [{"type": "text", "text": "ok"}]},
    ]
    snap = group_messages_into_snapshot(messages)
    assert len(snap.items) >= 1
    turn = next(i for i in snap.items if i.kind == "turn")
    assert turn.prompt == "plan a trip"
    # assistant step should have text + thinking + tool frames
    kinds = [f.kind for s in turn.steps for f in s.frames]
    assert "text" in kinds and "thinking" in kinds and "tool" in kinds


def test_fold_wire_record_facts_basic():
    base = AgentTranscriptSnapshot(items=[], tasks=[], interactions=[], attachments=[], todos=[], prompts=[], meta=__import__("qingxiaotuan.ports.transcript.model", fromlist=["TranscriptMeta"]).TranscriptMeta())
    records = [
        {"type": "goal.create", "objective": "ship it", "time": 1000},
        {"type": "task.started", "info": {"taskId": "tk1", "kind": "process", "status": "running"}, "outputTail": "log"},
    ]
    out = fold_wire_record_facts(records, base)
    assert len(out.tasks) == 1
    assert out.tasks[0].task_id == "tk1"
    assert out.meta.goal is not None and out.meta.goal.objective == "ship it"


def test_is_plain_agent_id():
    assert is_plain_agent_id("agent-1.X")
    assert not is_plain_agent_id("..")
    assert not is_plain_agent_id("../evil")


def test_filesystem_round_trip_via_tmp_path(tmp_path):
    tr = AgentTranscript("agent1")
    tr.apply(_build_turn_ops())
    snap = tr.snapshot()
    path = tmp_path / "snap.json"
    path.write_text(dump_snapshot(snap), encoding="utf-8")
    loaded = load_snapshot(path.read_text(encoding="utf-8"))
    assert loaded == snap
