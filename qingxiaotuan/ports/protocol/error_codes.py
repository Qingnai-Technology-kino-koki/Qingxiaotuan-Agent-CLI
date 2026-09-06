"""Protocol error codes and their canonical ``domain.reason`` labels.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

Integer namespaces (see the TS source for the full legend):
  - 0      success
  - 4xxxx  client errors (HTTP-4xx analog)
  - 5xxxx  daemon internal errors
  - 6xxxx  tool runtime
  - 7xxxx  LLM provider passthrough (msg = upstream text)
  - 8xxxx  MCP server passthrough (msg = upstream text)
  - 9xxxx  reserved
"""

from __future__ import annotations

from enum import IntEnum


class ErrorCode(IntEnum):
    """Integer error codes. Values mirror ``error-codes.ts`` exactly."""

    SUCCESS = 0

    VALIDATION_FAILED = 40001
    REQUEST_MALFORMED = 40002

    AUTH_PROVISIONING_REQUIRED = 40110
    AUTH_TOKEN_MISSING = 40111
    AUTH_TOKEN_UNAUTHORIZED = 40112
    AUTH_MODEL_NOT_RESOLVED = 40113

    SESSION_NOT_FOUND = 40401
    PROMPT_NOT_FOUND = 40402
    MESSAGE_NOT_FOUND = 40403
    APPROVAL_NOT_FOUND = 40404
    QUESTION_NOT_FOUND = 40405
    TASK_NOT_FOUND = 40406
    FILE_NOT_FOUND = 40407
    MCP_SERVER_NOT_FOUND = 40408
    FS_PATH_NOT_FOUND = 40409
    WORKSPACE_NOT_FOUND = 40410
    FS_PERMISSION_DENIED = 40411
    PROVIDER_NOT_FOUND = 40412
    MODEL_NOT_FOUND = 40413
    TERMINAL_NOT_FOUND = 40414
    SKILL_NOT_FOUND = 40415
    TOOL_CALL_NOT_FOUND = 40416

    SESSION_BUSY = 40901
    APPROVAL_ALREADY_RESOLVED = 40902
    PROMPT_ALREADY_COMPLETED = 40903
    TASK_ALREADY_FINISHED = 40904
    MCP_ALREADY_CONNECTED = 40905
    FS_IS_DIRECTORY = 40906
    FS_IS_BINARY = 40907
    FS_GIT_UNAVAILABLE = 40908
    QUESTION_DISMISSED = 40909
    COMPACTION_UNABLE = 40910
    SESSION_UNDO_UNAVAILABLE = 40911
    SKILL_NOT_ACTIVATABLE = 40912

    GOAL_ALREADY_EXISTS = 40913
    GOAL_NOT_FOUND = 40914
    GOAL_STATUS_INVALID = 40915
    GOAL_NOT_RESUMABLE = 40916
    GOAL_OBJECTIVE_EMPTY = 40917
    GOAL_OBJECTIVE_TOO_LONG = 40918
    FS_ALREADY_EXISTS = 40919
    GOAL_UNSUPPORTED_AGENT = 40920
    PROMPT_ID_CONFLICT = 40927

    APPROVAL_EXPIRED = 41001
    QUESTION_EXPIRED = 41002
    FILE_EXPIRED = 41003

    FILE_TOO_LARGE = 41301
    FS_TOO_LARGE = 41302
    FS_TOO_MANY_RESULTS = 41303
    FS_PATH_ESCAPES_SESSION = 41304
    FS_GREP_TIMEOUT = 41305

    FS_WATCH_LIMIT_EXCEEDED = 42902

    INTERNAL_ERROR = 50001
    PERSISTENCE_FAILURE = 50003

    TOOL_EXECUTION_FAILED = 60001
    TOOL_NOT_AVAILABLE = 60002


# Reserved (intentionally unallocated; do NOT reuse for new variants):
#   40101 auth.invalid_token
#   40102 auth.missing_token
#   40103 auth.forbidden_origin
#   42901 rate.limited
#   50002 protocol.version_mismatch
_RESERVED_CODES = frozenset({40101, 40102, 40103, 42901, 50002})


ErrorCodeReason: dict[ErrorCode, str] = {
    ErrorCode.SUCCESS: "success",
    ErrorCode.VALIDATION_FAILED: "validation.failed",
    ErrorCode.REQUEST_MALFORMED: "request.malformed",
    ErrorCode.AUTH_PROVISIONING_REQUIRED: "auth.provisioning_required",
    ErrorCode.AUTH_TOKEN_MISSING: "auth.token_missing",
    ErrorCode.AUTH_TOKEN_UNAUTHORIZED: "auth.token_unauthorized",
    ErrorCode.AUTH_MODEL_NOT_RESOLVED: "auth.model_not_resolved",
    ErrorCode.SESSION_NOT_FOUND: "session.not_found",
    ErrorCode.PROMPT_NOT_FOUND: "prompt.not_found",
    ErrorCode.MESSAGE_NOT_FOUND: "message.not_found",
    ErrorCode.APPROVAL_NOT_FOUND: "approval.not_found",
    ErrorCode.QUESTION_NOT_FOUND: "question.not_found",
    ErrorCode.TASK_NOT_FOUND: "task.not_found",
    ErrorCode.FILE_NOT_FOUND: "file.not_found",
    ErrorCode.MCP_SERVER_NOT_FOUND: "mcp.server_not_found",
    ErrorCode.FS_PATH_NOT_FOUND: "fs.path_not_found",
    ErrorCode.WORKSPACE_NOT_FOUND: "workspace.not_found",
    ErrorCode.FS_PERMISSION_DENIED: "fs.permission_denied",
    ErrorCode.PROVIDER_NOT_FOUND: "provider.not_found",
    ErrorCode.MODEL_NOT_FOUND: "model.not_found",
    ErrorCode.TERMINAL_NOT_FOUND: "terminal.not_found",
    ErrorCode.SKILL_NOT_FOUND: "skill.not_found",
    ErrorCode.TOOL_CALL_NOT_FOUND: "tool_call.not_found",
    ErrorCode.SESSION_BUSY: "session.busy",
    ErrorCode.APPROVAL_ALREADY_RESOLVED: "approval.already_resolved",
    ErrorCode.PROMPT_ALREADY_COMPLETED: "prompt.already_completed",
    ErrorCode.TASK_ALREADY_FINISHED: "task.already_finished",
    ErrorCode.MCP_ALREADY_CONNECTED: "mcp.already_connected",
    ErrorCode.FS_IS_DIRECTORY: "fs.is_directory",
    ErrorCode.FS_IS_BINARY: "fs.is_binary",
    ErrorCode.FS_GIT_UNAVAILABLE: "fs.git_unavailable",
    ErrorCode.QUESTION_DISMISSED: "question.dismissed",
    ErrorCode.COMPACTION_UNABLE: "compaction.unable",
    ErrorCode.SESSION_UNDO_UNAVAILABLE: "session.undo_unavailable",
    ErrorCode.SKILL_NOT_ACTIVATABLE: "skill.not_activatable",
    ErrorCode.GOAL_ALREADY_EXISTS: "goal.already_exists",
    ErrorCode.GOAL_NOT_FOUND: "goal.not_found",
    ErrorCode.GOAL_STATUS_INVALID: "goal.status_invalid",
    ErrorCode.GOAL_NOT_RESUMABLE: "goal.not_resumable",
    ErrorCode.GOAL_OBJECTIVE_EMPTY: "goal.objective_empty",
    ErrorCode.GOAL_OBJECTIVE_TOO_LONG: "goal.objective_too_long",
    ErrorCode.FS_ALREADY_EXISTS: "fs.already_exists",
    ErrorCode.GOAL_UNSUPPORTED_AGENT: "goal.unsupported_agent",
    ErrorCode.PROMPT_ID_CONFLICT: "prompt.id_conflict",
    ErrorCode.APPROVAL_EXPIRED: "approval.expired",
    ErrorCode.QUESTION_EXPIRED: "question.expired",
    ErrorCode.FILE_EXPIRED: "file.expired",
    ErrorCode.FILE_TOO_LARGE: "file.too_large",
    ErrorCode.FS_TOO_LARGE: "fs.too_large",
    ErrorCode.FS_TOO_MANY_RESULTS: "fs.too_many_results",
    ErrorCode.FS_PATH_ESCAPES_SESSION: "fs.path_escapes_session",
    ErrorCode.FS_GREP_TIMEOUT: "fs.grep_timeout",
    ErrorCode.FS_WATCH_LIMIT_EXCEEDED: "fs.watch_limit_exceeded",
    ErrorCode.INTERNAL_ERROR: "internal.error",
    ErrorCode.PERSISTENCE_FAILURE: "persistence.failure",
    ErrorCode.TOOL_EXECUTION_FAILED: "tool.execution_failed",
    ErrorCode.TOOL_NOT_AVAILABLE: "tool.not_available",
}


def reason_for(code: int) -> str | None:
    """Return the canonical ``domain.reason`` label for a numeric code."""
    try:
        return ErrorCodeReason.get(ErrorCode(code))
    except ValueError:
        return None


def code_for(reason: str) -> int | None:
    """Return the numeric code for a canonical ``domain.reason`` label."""
    for code, label in ErrorCodeReason.items():
        if label == reason:
            return int(code)
    return None
