"""青小团 protocol port — foundational, dependency-free core.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``) (envelope, error-codes,
approval, display, asyncapi, message, events, ws-control, time, question,
pagination, request-id). Stdlib-only; no third-party deps.
"""

from __future__ import annotations

from .envelope import (
    Envelope,
    err_envelope,
    ok_envelope,
    parse_envelope,
)
from .error_codes import (
    ErrorCode,
    ErrorCodeReason,
    code_for,
    reason_for,
)
from .time import (
    IsoDateTime,
    is_iso_date_time,
    normalize_iso_date_time,
    now_iso_date_time,
)
from .request_id import (
    is_ulid,
    parse_or_generate_request_id,
    ulid,
)
from .approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalScope,
)
from .display import (
    parse_tool_input_display,
    parse_tool_result_display,
)
from .question import (
    QuestionAnswer,
    QuestionAnswerMethod,
    QuestionItem,
    QuestionOption,
    QuestionRequest,
    QuestionResponse,
    parse_question_answer,
)
from .pagination import (
    CursorQuery,
    PageResponse,
)
from .message import (
    Message,
    MessageRole,
    parse_image_source,
    parse_message_content,
)
from .events import (
    EVENT_TYPES,
    ErrorEvent,
    Event,
    FinishReason,
    GoalActor,
    GoalStatus,
    parse_event,
    PermissionMode,
    SkillSource,
    TaskLifecycleStatus,
    TokenUsage,
    TurnEndReason,
    TurnInterruptReason,
    UsageStatus,
    WarningEvent,
)
from .ws_control import (
    WS_PROTOCOL_VERSION,
    SessionCursor,
    WsOperationDefinition,
    get_client_control_operation,
    ws_operations,
    client_control_operations,
    server_system_operations,
)
from .asyncapi import (
    AsyncApiDocumentOptions,
    create_async_api_document,
    message_id,
    title_from_name,
)

__all__ = [
    # envelope
    "Envelope",
    "ok_envelope",
    "err_envelope",
    "parse_envelope",
    # error-codes
    "ErrorCode",
    "ErrorCodeReason",
    "reason_for",
    "code_for",
    # time
    "IsoDateTime",
    "is_iso_date_time",
    "normalize_iso_date_time",
    "now_iso_date_time",
    # request-id
    "is_ulid",
    "parse_or_generate_request_id",
    "ulid",
    # approval
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalScope",
    # display
    "parse_tool_input_display",
    "parse_tool_result_display",
    # (ToolInputDisplay / ToolResultDisplay are unions of dataclasses in display.py)
    # question
    "QuestionAnswer",
    "QuestionAnswerMethod",
    "QuestionItem",
    "QuestionOption",
    "QuestionRequest",
    "QuestionResponse",
    "parse_question_answer",
    # pagination
    "CursorQuery",
    "PageResponse",
    # message
    "Message",
    "MessageRole",
    "parse_image_source",
    "parse_message_content",
    # events
    "EVENT_TYPES",
    "ErrorEvent",
    "Event",
    "FinishReason",
    "GoalActor",
    "GoalStatus",
    "parse_event",
    "PermissionMode",
    "SkillSource",
    "TaskLifecycleStatus",
    "TokenUsage",
    "TurnEndReason",
    "TurnInterruptReason",
    "UsageStatus",
    "WarningEvent",
    # ws-control
    "WS_PROTOCOL_VERSION",
    "SessionCursor",
    "WsOperationDefinition",
    "get_client_control_operation",
    "ws_operations",
    "client_control_operations",
    "server_system_operations",
    # asyncapi
    "AsyncApiDocumentOptions",
    "create_async_api_document",
    "message_id",
    "title_from_name",
]
