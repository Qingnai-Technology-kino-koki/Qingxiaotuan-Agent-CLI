"""Behavioral tests for the ported protocol core (qingxiaotuan/ports/protocol).

Mirrors the behavioral intent of the TS ``__tests__`` for envelope / error-codes
/ approval / display, plus coverage for the other foundational modules.
"""

from __future__ import annotations

import json

import pytest

from qingxiaotuan.ports.protocol import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalScope,
    AsyncApiDocumentOptions,
    ErrorCode,
    ErrorCodeReason,
    Envelope,
    EVENT_TYPES,
    Message,
    MessageRole,
    PageResponse,
    CursorQuery,
    QuestionAnswerMethod,
    QuestionRequest,
    QuestionResponse,
    SessionCursor,
    TokenUsage,
    UsageStatus,
    WS_PROTOCOL_VERSION,
    code_for,
    create_async_api_document,
    err_envelope,
    is_iso_date_time,
    is_ulid,
    message_id,
    normalize_iso_date_time,
    now_iso_date_time,
    ok_envelope,
    parse_envelope,
    parse_event,
    parse_message_content,
    parse_or_generate_request_id,
    parse_question_answer,
    parse_tool_input_display,
    parse_tool_result_display,
    reason_for,
    title_from_name,
    ulid,
)


# --- envelope ----------------------------------------------------------------


def test_ok_envelope_shape_and_wire():
    built = ok_envelope({"id": "sess_1"}, "req_y")
    assert built.to_dict() == {
        "code": 0,
        "msg": "success",
        "data": {"id": "sess_1"},
        "request_id": "req_y",
    }
    # Byte-identical to the daemon helper's JSON.stringify output.
    assert json.dumps(built.to_dict(), separators=(",", ":"), ensure_ascii=False) == (
        '{"code":0,"msg":"success","data":{"id":"sess_1"},"request_id":"req_y"}'
    )


def test_err_envelope_data_null_and_wire():
    built = err_envelope(40001, "validation failed", "req_z")
    assert built.code == 40001
    assert built.data is None
    assert json.dumps(built.to_dict(), separators=(",", ":"), ensure_ascii=False) == (
        '{"code":40001,"msg":"validation failed","data":null,"request_id":"req_z"}'
    )


def test_err_envelope_omits_stack_when_absent():
    without = err_envelope(ErrorCode.INTERNAL_ERROR, "boom", "req_s")
    assert "stack" not in without.to_dict()
    assert json.dumps(without.to_dict(), separators=(",", ":"), ensure_ascii=False) == (
        '{"code":50001,"msg":"boom","data":null,"request_id":"req_s"}'
    )


def test_err_envelope_surfaces_stack_when_present():
    err = ValueError("boom")
    with_stack = err_envelope(ErrorCode.INTERNAL_ERROR, "boom", "req_s", "trace...")
    assert with_stack.stack == "trace..."
    assert json.dumps(with_stack.to_dict(), separators=(",", ":"), ensure_ascii=False).endswith(
        '"stack":"trace..."}'
    )


def test_envelope_parse_round_trip():
    env = err_envelope(ErrorCode.SESSION_NOT_FOUND, "session abc123 does not exist", "req_x")
    parsed = parse_envelope(env.to_dict())
    assert parsed.code == 40401
    assert parsed.data is None
    assert parsed.request_id == "req_x"


def test_envelope_parse_rejects_bad_shapes():
    with pytest.raises(ValueError):
        parse_envelope({"code": 1.5, "msg": "x", "data": None, "request_id": "r"})
    with pytest.raises(ValueError):
        parse_envelope({"code": 0, "msg": "success", "data": None})


# --- error-codes -------------------------------------------------------------


def test_error_code_canonical_values():
    assert ErrorCode.SUCCESS == 0
    assert ErrorCode.VALIDATION_FAILED == 40001
    assert ErrorCode.SESSION_NOT_FOUND == 40401
    assert ErrorCode.GOAL_UNSUPPORTED_AGENT == 40920
    assert ErrorCode.APPROVAL_EXPIRED == 41001
    assert ErrorCode.FS_WATCH_LIMIT_EXCEEDED == 42902
    assert ErrorCode.INTERNAL_ERROR == 50001
    assert ErrorCode.TOOL_EXECUTION_FAILED == 60001


def test_error_code_reason_map():
    assert ErrorCodeReason[ErrorCode.SESSION_NOT_FOUND] == "session.not_found"
    assert ErrorCodeReason[ErrorCode.PROVIDER_NOT_FOUND] == "provider.not_found"
    assert ErrorCodeReason[ErrorCode.MODEL_NOT_FOUND] == "model.not_found"
    assert ErrorCodeReason[ErrorCode.FS_WATCH_LIMIT_EXCEEDED] == "fs.watch_limit_exceeded"
    assert ErrorCodeReason[ErrorCode.GOAL_UNSUPPORTED_AGENT] == "goal.unsupported_agent"


def test_error_code_lookup_helpers():
    assert reason_for(40401) == "session.not_found"
    assert reason_for(50001) == "internal.error"
    assert reason_for(99999) is None
    assert code_for("session.not_found") == 40401
    assert code_for("validation.failed") == 40001
    assert code_for("does.not.exist") is None


def test_reserved_codes_absent():
    values = {int(c) for c in ErrorCode}
    for reserved in (40101, 40102, 40103, 42901, 50002):
        assert reserved not in values


# --- time --------------------------------------------------------------------


def test_iso_normalizes_offset_and_rejects_garbage():
    assert normalize_iso_date_time("2026-06-04T18:30:00+08:00") == "2026-06-04T10:30:00.000Z"
    assert normalize_iso_date_time("2026-06-04T10:30:00Z") == "2026-06-04T10:30:00.000Z"
    assert is_iso_date_time("2026-06-04T10:30:00.000Z")
    assert not is_iso_date_time("2026-06-04 10:30:00")
    assert not is_iso_date_time("not-a-date")
    with pytest.raises(ValueError):
        normalize_iso_date_time("nope")
    assert isinstance(now_iso_date_time(), str)


# --- request-id --------------------------------------------------------------


def test_ulid_validation_and_generation():
    generated = ulid()
    assert is_ulid(generated)
    assert len(generated) == 26
    assert is_ulid("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert not is_ulid("01ARZ3NDEKTSV4RRFFQ69G5FA")  # too short
    assert not is_ulid("O" * 26)  # 'O' is excluded from the Crockford alphabet
    assert not is_ulid("I" * 26)  # 'I' is excluded from the Crockford alphabet
    assert parse_or_generate_request_id(generated) == generated
    assert is_ulid(parse_or_generate_request_id(None))
    assert is_ulid(parse_or_generate_request_id("random-not-ulid"))


# --- approval ----------------------------------------------------------------


def test_approval_request_construction_and_normalization():
    raw = {
        "approval_id": "01J0000000APPROVAL",
        "session_id": "sess_x",
        "tool_call_id": "tc_1",
        "tool_name": "shell.run",
        "action": "Run `rm -rf foo/`",
        "tool_input_display": {"kind": "command", "command": "rm -rf foo/"},
        "created_at": "2026-06-04T18:30:00+08:00",
        "expires_at": "2026-06-04T10:31:00Z",
        "turn_id": 42,
    }
    req = ApprovalRequest.from_dict(raw)
    assert req.approval_id == "01J0000000APPROVAL"
    assert req.turn_id == 42
    assert req.created_at == "2026-06-04T10:30:00.000Z"
    # tool_input_display passes through as opaque value.
    assert req.tool_input_display["kind"] == "command"
    # round-trip
    assert ApprovalRequest.from_dict(req.to_dict()).approval_id == req.approval_id


def test_approval_request_rejects_missing_id():
    with pytest.raises(ValueError):
        ApprovalRequest.from_dict(
            {
                "session_id": "s",
                "tool_call_id": "t",
                "tool_name": "n",
                "action": "a",
                "tool_input_display": {},
                "created_at": "2026-06-04T10:30:00Z",
                "expires_at": "2026-06-04T10:31:00Z",
            }
        )


def test_approval_response_validation():
    minimal = ApprovalResponse.from_dict({"decision": "approved"})
    assert minimal.decision == ApprovalDecision.APPROVED
    assert minimal.to_dict() == {"decision": "approved"}

    full = ApprovalResponse.from_dict(
        {
            "decision": "approved",
            "scope": "session",
            "feedback": "looks good",
            "selected_label": "Run command",
        }
    )
    assert full.scope == ApprovalScope.SESSION
    assert full.to_dict()["scope"] == "session"

    with pytest.raises(ValueError):
        ApprovalResponse.from_dict({"decision": "maybe"})


# --- display -----------------------------------------------------------------


def test_display_input_command_parse():
    disp = parse_tool_input_display({"kind": "command", "command": "pwd", "language": "bash"})
    assert disp.kind == "command"
    assert disp.command == "pwd"
    assert disp.language == "bash"
    assert disp.to_dict() == {"kind": "command", "command": "pwd", "language": "bash"}


def test_display_input_requires_command():
    with pytest.raises(ValueError):
        parse_tool_input_display({"kind": "command", "command": ""})


def test_display_result_text_parse():
    res = parse_tool_result_display({"kind": "text", "text": "hello", "truncated": True})
    assert res.kind == "text"
    assert res.text == "hello"
    assert res.truncated is True
    assert res.to_dict() == {"kind": "text", "text": "hello", "truncated": True}


def test_display_unknown_kind_rejected():
    with pytest.raises(ValueError):
        parse_tool_input_display({"kind": "nope"})
    with pytest.raises(ValueError):
        parse_tool_result_display({"kind": "nope"})


# --- question ----------------------------------------------------------------


def test_question_answer_variants():
    assert parse_question_answer({"kind": "single", "option_id": "a"}).option_id == "a"
    assert parse_question_answer({"kind": "multi", "option_ids": ["a", "b"]}).option_ids == ["a", "b"]
    assert parse_question_answer({"kind": "other", "text": "x"}).text == "x"
    assert parse_question_answer({"kind": "skipped"}).kind == "skipped"
    with pytest.raises(ValueError):
        parse_question_answer({"kind": "bogus"})


def test_question_response_round_trip():
    raw = {
        "answers": {
            "q1": {"kind": "single", "option_id": "a"},
            "q2": {"kind": "multi", "option_ids": ["x", "y"]},
        },
        "method": "click",
        "note": "ok",
    }
    resp = QuestionResponse.from_dict(raw)
    assert resp.method == QuestionAnswerMethod.CLICK
    assert resp.answers["q2"].option_ids == ["x", "y"]
    assert resp.to_dict()["answers"]["q2"] == {"kind": "multi", "option_ids": ["x", "y"]}


def test_question_request_validation():
    req = QuestionRequest.from_dict(
        {
            "question_id": "q1",
            "session_id": "s1",
            "questions": [{"id": "q", "question": "?", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}],
            "created_at": "2026-06-04T10:30:00Z",
        }
    )
    assert req.questions[0].options[1].label == "B"
    with pytest.raises(ValueError):
        QuestionRequest.from_dict(
            {"question_id": "q1", "session_id": "s1", "questions": [], "created_at": "2026-06-04T10:30:00Z"}
        )


# --- pagination --------------------------------------------------------------


def test_cursor_query_mutual_exclusion_and_bounds():
    with pytest.raises(ValueError):
        CursorQuery.from_dict({"before_id": "a", "after_id": "b"})
    with pytest.raises(ValueError):
        CursorQuery.from_dict({"page_size": 200})
    cq = CursorQuery.from_dict({"after_id": "x", "page_size": 50})
    assert cq.after_id == "x"
    assert cq.to_dict() == {"after_id": "x", "page_size": 50}
    assert PageResponse(items=[1, 2], has_more=True).to_dict() == {"items": [1, 2], "has_more": True}


# --- message -----------------------------------------------------------------


def test_message_content_text_and_file():
    text = parse_message_content({"type": "text", "text": "hi"})
    assert text.text == "hi"
    # exactly-one-of file_id / path
    fc = parse_message_content(
        {"type": "file", "file_id": "f1", "name": "n", "media_type": "image/png", "size": 10}
    )
    assert fc.file_id == "f1"
    with pytest.raises(ValueError):
        parse_message_content({"type": "file", "file_id": "f1", "path": "p"})
    with pytest.raises(ValueError):
        parse_message_content({"type": "file", "file_id": "f1"})


def test_message_content_image_source():
    img = parse_message_content({"type": "image", "source": {"kind": "url", "url": "http://x"}})
    assert img.source.url == "http://x"
    with pytest.raises(ValueError):
        parse_message_content({"type": "image", "source": {"kind": "url"}})


def test_message_round_trip():
    msg = Message.from_dict(
        {
            "id": "m1",
            "session_id": "s1",
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
            "created_at": "2026-06-04T10:30:00Z",
        }
    )
    assert msg.role == MessageRole.USER
    out = msg.to_dict()
    assert out["role"] == "user"
    assert out["content"] == [{"type": "text", "text": "hello"}]
    reparsed = Message.from_dict(out)
    assert reparsed.id == "m1"


# --- events ------------------------------------------------------------------


def test_event_parse_core_families():
    assert parse_event({"type": "assistant.delta", "turn_id": 1, "delta": "hi"}).delta == "hi"
    completed = parse_event({"type": "shell.completed", "command_id": "c", "is_error": False})
    assert completed.is_error is False
    started = parse_event(
        {"type": "turn.started", "turn_id": 1, "origin": {"kind": "user"}, "prompt_id": "p1"}
    )
    assert started.prompt_id == "p1"
    # unknown type -> generic Event fallback, still round-trips
    generic = parse_event({"type": "unknown.event", "turn_id": 1})
    assert generic.type == "unknown.event"
    assert generic.to_dict()["type"] == "unknown.event"


def test_event_registry_non_empty():
    assert "turn.ended" in EVENT_TYPES
    assert "event.session.created" in EVENT_TYPES


# --- asyncapi ----------------------------------------------------------------


def test_asyncapi_helpers():
    assert message_id("client_hello") == "client_hello"
    assert message_id("session.event") == "session_event"
    assert title_from_name("client_hello") == "Client Hello"
    assert title_from_name("tool.call.started") == "Tool Call Started"


def test_create_async_api_document_structure():
    doc = create_async_api_document()
    assert doc["asyncapi"] == "3.1.0"
    assert doc["info"]["title"] == "Kimi Code WebSocket API"
    assert doc["channels"]["kimiCodeWebSocket"]["address"] == "/api/v1/ws"
    # every operation surfaces as a message reference
    ops = doc["operations"]
    client_refs = ops["receiveClientMessages"]["messages"]
    server_refs = ops["sendServerMessages"]["messages"]
    assert {"$ref": "#/components/messages/client_hello"} in client_refs
    assert {"$ref": "#/components/messages/session_event"} in server_refs
    # ack messages present for operations that define an ack schema
    assert {"$ref": "#/components/messages/subscribe_ack"} in server_refs
    # payloads are attached (not just refs)
    assert doc["components"]["messages"]["client_hello"]["payload"]["type"] == "object"


def test_create_async_api_document_options():
    doc = create_async_api_document(
        AsyncApiDocumentOptions(title="X", version="9.9.9", server_host="h", server_protocol="wss", ws_path="/p")
    )
    assert doc["info"]["title"] == "X"
    assert doc["info"]["version"] == "9.9.9"
    assert doc["servers"]["local"]["host"] == "h"
    assert doc["servers"]["local"]["protocol"] == "wss"
    assert doc["channels"]["kimiCodeWebSocket"]["address"] == "/p"


def test_ws_protocol_version_and_cursor():
    assert WS_PROTOCOL_VERSION == 2
    assert SessionCursor(seq=1, epoch="e").to_dict() == {"seq": 1, "epoch": "e"}
    assert SessionCursor(seq=1).to_dict() == {"seq": 1}
