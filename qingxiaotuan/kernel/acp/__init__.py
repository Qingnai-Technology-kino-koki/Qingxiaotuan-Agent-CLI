"""qingxiaotuan.kernel.acp —— ACP (Agent Client Protocol) server 的 Python 自研实现（精简同步版）。

导出顶层 API：
- ``AcpServer`` / ``run_acp_server_stdio``：server 主体与 stdio 入口。
- ``AcpSession``：单 session 的 prompt 驱动 + 事件翻译。
- ``session_update`` / ``request_permission``：``session/update`` 通知构造器与
  ``session/request_permission`` 参数构造器（也分别是 AcpConn 的两个方法）。
- ``AcpInteractionBridge``：approval 反向 RPC 桥。
- ``KernelAcpBridge``：把既有 ``Agent``/``AcpServer`` 适配为 agent_provider。
- 以及 codec / version / protocol / convert / events_map 的子工具。

零新依赖（仅 asyncio / json / typing）。stdout 是协议通道，server 运行时会把 console 重定向 stderr。
"""

from __future__ import annotations

from . import version as version
from .bridge import KernelAcpBridge
from .version import negotiate_version
from .codec import (
    LineBuffer,
    CANCEL_REQUEST_NOTIFICATION,
    encode_cancel_request,
    encode_error,
    encode_notification,
    encode_request,
    encode_response,
    is_notification,
    is_request,
    is_response,
    parse_frame,
)
from .convert import acp_blocks_to_content_parts, content_parts_to_acp, tool_result_to_acp
from .events_map import (
    ToolCallRegistry,
    acp_tool_call_id,
    assistant_delta_to_session_update,
    infer_tool_kind,
    stringify_args,
    thinking_delta_to_session_update,
    tool_call_delta_update,
    tool_call_lazy_create,
    tool_call_start_to_session_update,
    tool_call_started_upgrade,
    tool_progress_to_session_update,
    tool_result_to_session_update,
    turn_end_reason_to_stop_reason,
)
from .interaction_bridge import AcpInteractionBridge, approval_request_to_permission_options
from .protocol import (
    AcpConn,
    ApprovalResponse,
    RequestPermissionResponse,
    agent_message_chunk,
    agent_thought_chunk,
    available_commands_update,
    config_option_update,
    current_mode_update,
    plan,
    plan_removed,
    plan_update,
    request_permission,
    session_info_update,
    session_update,
    tool_call,
    tool_call_update,
    usage_update,
    APPROVE_ALWAYS_OPTION_ID,
    APPROVE_ONCE_OPTION_ID,
    AUTHENTICATE,
    INITIALIZE,
    LOGOUT,
    PLAN_APPROVE_OPTION_ID,
    PLAN_REJECT_AND_EXIT_OPTION_ID,
    PLAN_REVISE_OPTION_ID,
    REJECT_OPTION_ID,
    SESSION_CANCEL,
    SESSION_CLOSE,
    SESSION_DELETE,
    SESSION_FORK,
    SESSION_LIST,
    SESSION_LOAD,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_RESUME,
    SESSION_REQUEST_PERMISSION,
    SESSION_SET_MODE,
    SESSION_SET_MODEL,
    SESSION_UPDATE,
)
from .server import AcpServer, run_acp_server_stdio
from .session import AcpSession, AgentProvider

__all__ = [
    "AcpServer",
    "run_acp_server_stdio",
    "AcpSession",
    "AgentProvider",
    "AcpInteractionBridge",
    "KernelAcpBridge",
    "AcpConn",
    "ApprovalResponse",
    "RequestPermissionResponse",
    "session_update",
    "request_permission",
    # codec
    "LineBuffer",
    "encode_request",
    "encode_response",
    "encode_notification",
    "encode_error",
    "encode_cancel_request",
    "parse_frame",
    "is_request",
    "is_notification",
    "is_response",
    "CANCEL_REQUEST_NOTIFICATION",
    # version
    "version",
    "negotiate_version",
    # protocol constants
    "INITIALIZE",
    "SESSION_NEW",
    "SESSION_LOAD",
    "SESSION_RESUME",
    "SESSION_LIST",
    "SESSION_CLOSE",
    "SESSION_DELETE",
    "SESSION_FORK",
    "SESSION_PROMPT",
    "SESSION_CANCEL",
    "SESSION_SET_MODE",
    "SESSION_SET_MODEL",
    "AUTHENTICATE",
    "LOGOUT",
    "SESSION_UPDATE",
    "SESSION_REQUEST_PERMISSION",
    # permission options
    "APPROVE_ONCE_OPTION_ID",
    "APPROVE_ALWAYS_OPTION_ID",
    "REJECT_OPTION_ID",
    "PLAN_APPROVE_OPTION_ID",
    "PLAN_REVISE_OPTION_ID",
    "PLAN_REJECT_AND_EXIT_OPTION_ID",
    # convert
    "acp_blocks_to_content_parts",
    "content_parts_to_acp",
    "tool_result_to_acp",
    # events_map
    "ToolCallRegistry",
    "acp_tool_call_id",
    "assistant_delta_to_session_update",
    "thinking_delta_to_session_update",
    "tool_call_lazy_create",
    "tool_call_start_to_session_update",
    "tool_call_started_upgrade",
    "tool_call_delta_update",
    "tool_progress_to_session_update",
    "tool_result_to_session_update",
    "turn_end_reason_to_stop_reason",
    "infer_tool_kind",
    "stringify_args",
    # session/update constructors
    "agent_message_chunk",
    "agent_thought_chunk",
    "tool_call",
    "tool_call_update",
    "available_commands_update",
    "current_mode_update",
    "config_option_update",
    "usage_update",
    "session_info_update",
    "plan",
    "plan_update",
    "plan_removed",
    "approval_request_to_permission_options",
]
