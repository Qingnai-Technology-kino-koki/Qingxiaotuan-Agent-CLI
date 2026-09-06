"""WebSocket control / system frame definitions.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

The TS source builds every operation's ``messageSchema`` from a ``z.ZodType``.
Without zod we represent each payload as a compact JSON-Schema dict (hand
written, field-faithful) so :mod:`asyncapi` can emit a complete AsyncAPI
document with real payload schemas. ``session_event`` carries the session event
envelope; its payload is referenced as an opaque object here (the full event
union lives in :mod:`events`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

WS_PROTOCOL_VERSION = 2


def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def _str() -> dict[str, Any]:
    return {"type": "string"}


def _int() -> dict[str, Any]:
    return {"type": "integer"}


@dataclass
class SessionCursor:
    seq: int
    epoch: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"seq": self.seq}
        if self.epoch is not None:
            out["epoch"] = self.epoch
        return out


WsOperationDirection = Literal["client_to_server", "server_to_client"]
WsOperationKind = Literal["control", "system", "event"]


@dataclass
class WsOperationDefinition:
    type: str
    direction: WsOperationDirection
    kind: WsOperationKind
    message_schema: dict[str, Any]
    description: str
    ack_schema: Optional[dict[str, Any]] = None


_client_hello_payload = _obj(
    {
        "client_id": _str(),
        "subscriptions": {"type": "array", "items": _str()},
        "cursors": {"type": "object"},
        "agent_filter": {"type": "object"},
    },
    ["client_id"],
)

_subscribe_payload = _obj(
    {
        "session_ids": {"type": "array", "items": _str()},
        "cursors": {"type": "object"},
        "watch_fs": {"type": "object"},
        "agent_filter": {"type": "object"},
    },
    ["session_ids"],
)

_subscribe_ack_payload = _obj(
    {
        "accepted": {"type": "array", "items": _str()},
        "not_found": {"type": "array", "items": _str()},
        "resync_required": {"type": "array", "items": _str()},
        "cursors": {"type": "object"},
    },
    ["accepted", "not_found", "resync_required"],
)

_watch_fs_add_payload = _obj(
    {"session_id": _str(), "paths": {"type": "array", "items": _str()}, "recursive": {"type": "boolean"}},
    ["session_id", "paths"],
)

_watch_fs_remove_payload = _obj(
    {"session_id": _str(), "paths": {"type": "array", "items": _str()}},
    ["session_id", "paths"],
)

_watch_fs_ack_payload = _obj(
    {"watched_paths": {"type": "array", "items": _str()}, "current_count": _int()},
    [],
)

_abort_payload = _obj({"session_id": _str(), "prompt_id": _str()}, ["session_id", "prompt_id"])
_abort_ack_payload = _obj(
    {"aborted": {"type": "boolean"}, "at_seq": _int()}, []
)

_terminal_attach_payload = _obj(
    {"session_id": _str(), "terminal_id": _str(), "since_seq": _int()},
    ["session_id", "terminal_id"],
)
_terminal_attach_ack_payload = _obj({"attached": {"const": True}, "replayed": _int()}, ["attached", "replayed"])

_terminal_detach_payload = _obj(
    {"session_id": _str(), "terminal_id": _str()}, ["session_id", "terminal_id"]
)
_terminal_detach_ack_payload = _obj({"detached": {"const": True}}, ["detached"])

_terminal_input_payload = _obj(
    {"session_id": _str(), "terminal_id": _str(), "data": _str()},
    ["session_id", "terminal_id", "data"],
)
_terminal_input_ack_payload = _obj({"accepted": {"const": True}}, ["accepted"])

_terminal_resize_payload = _obj(
    {"session_id": _str(), "terminal_id": _str(), "cols": _int(), "rows": _int()},
    ["session_id", "terminal_id", "cols", "rows"],
)
_terminal_resize_ack_payload = _obj({"resized": {"const": True}}, ["resized"])

_terminal_close_payload = _obj(
    {"session_id": _str(), "terminal_id": _str()}, ["session_id", "terminal_id"]
)
_terminal_close_ack_payload = _obj({"closed": {"const": True}}, ["closed"])

_ping_payload = _obj({"nonce": _str()}, ["nonce"])
_pong_payload = _obj({"nonce": _str()}, ["nonce"])

_server_hello_payload = _obj(
    {
        "ws_connection_id": _str(),
        "protocol_version": _int(),
        "heartbeat_ms": _int(),
        "max_event_buffer_size": _int(),
        "capabilities": _obj(
            {"event_batching": {"type": "boolean"}, "compression": {"type": "boolean"}},
            ["event_batching", "compression"],
        ),
    },
    ["ws_connection_id", "protocol_version", "max_event_buffer_size", "capabilities"],
)

_resync_required_payload = _obj(
    {
        "session_id": _str(),
        "reason": {"type": "string", "enum": ["buffer_overflow", "session_recreated", "epoch_changed"]},
        "current_seq": _int(),
        "epoch": _str(),
    },
    ["session_id", "reason", "current_seq"],
)

_ws_error_payload = _obj(
    {
        "code": _int(),
        "msg": _str(),
        "fatal": {"type": "boolean"},
        "request_id": _str(),
        "details": {},
    },
    ["code", "msg", "fatal"],
)

_session_event_payload = _obj(
    {
        "type": _str(),
        "seq": _int(),
        "epoch": _str(),
        "volatile": {"type": "boolean"},
        "offset": _int(),
        "session_id": _str(),
        "timestamp": _str(),
        "payload": {"type": "object"},
    },
    ["type", "seq", "timestamp", "payload"],
)


client_control_operations: list[WsOperationDefinition] = [
    WsOperationDefinition("client_hello", "client_to_server", "control", _client_hello_payload,
                         "Start a client session and optionally subscribe to existing daemon sessions.",
                         _obj({"accepted_subscriptions": {"type": "array", "items": _str()},
                               "resync_required": {"type": "array", "items": _str()},
                               "cursors": {"type": "object"}}, ["accepted_subscriptions", "resync_required"])),
    WsOperationDefinition("subscribe", "client_to_server", "control", _subscribe_payload,
                         "Subscribe the connection to one or more session event streams.", _subscribe_ack_payload),
    WsOperationDefinition("unsubscribe", "client_to_server", "control", _subscribe_payload,
                         "Remove one or more session event stream subscriptions.", _subscribe_ack_payload),
    WsOperationDefinition("watch_fs_add", "client_to_server", "control", _watch_fs_add_payload,
                         "Add filesystem watch paths for a subscribed session.", _watch_fs_ack_payload),
    WsOperationDefinition("watch_fs_remove", "client_to_server", "control", _watch_fs_remove_payload,
                         "Remove filesystem watch paths for a subscribed session.", _watch_fs_ack_payload),
    WsOperationDefinition("abort", "client_to_server", "control", _abort_payload,
                         "Abort a running prompt in a session.", _abort_ack_payload),
    WsOperationDefinition("terminal_attach", "client_to_server", "control", _terminal_attach_payload,
                         "Attach this connection to a terminal stream.", _terminal_attach_ack_payload),
    WsOperationDefinition("terminal_detach", "client_to_server", "control", _terminal_detach_payload,
                         "Detach this connection from a terminal stream.", _terminal_detach_ack_payload),
    WsOperationDefinition("terminal_input", "client_to_server", "control", _terminal_input_payload,
                         "Write raw input bytes to a terminal.", _terminal_input_ack_payload),
    WsOperationDefinition("terminal_resize", "client_to_server", "control", _terminal_resize_payload,
                         "Resize a terminal.", _terminal_resize_ack_payload),
    WsOperationDefinition("terminal_close", "client_to_server", "control", _terminal_close_payload,
                         "Close a terminal.", _terminal_close_ack_payload),
    WsOperationDefinition("pong", "client_to_server", "control", _pong_payload,
                         "Reply to a server ping with the same nonce.", None),
]

server_system_operations: list[WsOperationDefinition] = [
    WsOperationDefinition("server_hello", "server_to_client", "system", _server_hello_payload,
                         "Initial server greeting sent immediately after the socket opens.", None),
    WsOperationDefinition("ping", "server_to_client", "system", _ping_payload,
                         "Heartbeat ping sent by the server; clients must answer with pong.", None),
    WsOperationDefinition("resync_required", "server_to_client", "system", _resync_required_payload,
                         "Signals that a client must rebuild local session state from REST history.", None),
    WsOperationDefinition("error", "server_to_client", "system", _ws_error_payload,
                         "Server-side WebSocket protocol or runtime error.", None),
]

session_event_operation = WsOperationDefinition(
    "session_event", "server_to_client", "event", _session_event_payload,
    "Session-scoped agent event envelope; frame type is the payload event type.", None,
)

ws_operations: list[WsOperationDefinition] = [
    *client_control_operations,
    *server_system_operations,
    session_event_operation,
]


def get_client_control_operation(type: str) -> Optional[WsOperationDefinition]:
    """Return the client→server control operation definition for ``type``."""
    for operation in client_control_operations:
        if operation.type == type:
            return operation
    return None
