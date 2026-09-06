"""AsyncAPI document builder for the agent WebSocket API.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

The TS ``createAsyncApiDocument`` converts every ``wsOperations`` message schema
to JSON-Schema via ``z.toJSONSchema``. Here each operation already carries a
JSON-Schema ``message_schema`` (see :mod:`ws_control`), so the builder simply
assembles them into the AsyncAPI 3.1 document structure. The pure helpers
``message_id`` / ``title_from_name`` are ported verbatim.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from .ws_control import ws_operations

ASYNCAPI_VERSION = "3.1.0"
DEFAULT_TITLE = "Kimi Code WebSocket API"
DEFAULT_VERSION = "0.1.0"
DEFAULT_SERVER_HOST = "localhost"
DEFAULT_WS_PATH = "/api/v1/ws"
CHANNEL_ID = "kimiCodeWebSocket"


class AsyncApiDocumentOptions:
    def __init__(
        self,
        title: Optional[str] = None,
        version: Optional[str] = None,
        server_host: Optional[str] = None,
        server_protocol: Literal["ws", "wss"] = "ws",
        ws_path: Optional[str] = None,
    ) -> None:
        self.title = title
        self.version = version
        self.server_host = server_host
        self.server_protocol = server_protocol
        self.ws_path = ws_path


def message_id(type: str) -> str:
    """Normalize an operation type into a message id (e.g. ``client_hello``)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", type).strip("_")


def title_from_name(name: str) -> str:
    """Convert an operation type into a title-cased message title."""
    return " ".join(
        p[0].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", name) if p
    )


def _async_api_message(name: str, summary: str, schema: dict[str, Any]) -> dict[str, Any]:
    payload = dict(schema)
    payload.pop("$schema", None)
    return {
        "name": name,
        "title": title_from_name(name),
        "summary": summary,
        "contentType": "application/json",
        "payload": payload,
    }


def _build_messages() -> dict[str, Any]:
    messages: dict[str, Any] = {}
    for operation in ws_operations:
        mid = message_id(operation.type)
        messages[mid] = _async_api_message(operation.type, operation.description, operation.message_schema)
        if operation.ack_schema is not None:
            ack_id = f"{mid}_ack"
            messages[ack_id] = _async_api_message(
                f"{operation.type}.ack", f"Acknowledgement for {operation.type}.", operation.ack_schema
            )
    return messages


def _operation_message_refs(direction: str) -> list[dict[str, str]]:
    return [
        {"$ref": f"#/components/messages/{message_id(op.type)}"}
        for op in ws_operations
        if op.direction == direction
    ]


def _ack_message_refs() -> list[dict[str, str]]:
    return [
        {"$ref": f"#/components/messages/{message_id(op.type)}_ack"}
        for op in ws_operations
        if op.ack_schema is not None
    ]


def create_async_api_document(options: Optional[AsyncApiDocumentOptions] = None) -> dict[str, Any]:
    """Build an AsyncAPI 3.1 document describing the agent WS API."""
    opts = options or AsyncApiDocumentOptions()
    title = opts.title or DEFAULT_TITLE
    version = opts.version or DEFAULT_VERSION
    server_host = opts.server_host or DEFAULT_SERVER_HOST
    server_protocol = opts.server_protocol
    ws_path = opts.ws_path or DEFAULT_WS_PATH

    messages = _build_messages()
    channel_messages = {
        mid: {"$ref": f"#/components/messages/{mid}"} for mid in messages
    }

    return {
        "asyncapi": ASYNCAPI_VERSION,
        "info": {
            "title": title,
            "version": version,
            "description": (
                "WebSocket protocol for Kimi Code daemon control frames, "
                "acknowledgements, system frames, and session event streaming."
            ),
        },
        "defaultContentType": "application/json",
        "servers": {
            "local": {
                "host": server_host,
                "protocol": server_protocol,
                "pathname": ws_path,
                "description": "Kimi Code daemon WebSocket endpoint.",
            }
        },
        "channels": {
            CHANNEL_ID: {
                "address": ws_path,
                "servers": [{"$ref": "#/servers/local"}],
                "messages": channel_messages,
            }
        },
        "operations": {
            "receiveClientMessages": {
                "action": "receive",
                "channel": {"$ref": f"#/channels/{CHANNEL_ID}"},
                "messages": _operation_message_refs("client_to_server"),
            },
            "sendServerMessages": {
                "action": "send",
                "channel": {"$ref": f"#/channels/{CHANNEL_ID}"},
                "messages": [*_operation_message_refs("server_to_client"), *_ack_message_refs()],
            },
        },
        "components": {"messages": messages},
    }
