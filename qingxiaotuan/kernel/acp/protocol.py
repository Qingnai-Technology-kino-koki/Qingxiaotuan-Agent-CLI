"""ACP 协议原语：method 名常量、SessionUpdate 变体构造器、权限选项命名空间、conn 协议。

自研实现 —— ACP 协议消息编解码（
纯协议层。所有 ``session/update`` 通知统一形状为::

    {"sessionId": <str>, "update": {"sessionUpdate": <variant>, ...}}

本模块不引入任何依赖（仅 ``typing``），是 server / session / interaction_bridge 共享的
类型与构造器来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

# ===================================================================== method 名
# client -> server 请求
INITIALIZE = "initialize"
SESSION_NEW = "session/new"
SESSION_LOAD = "session/load"
SESSION_RESUME = "session/resume"
SESSION_LIST = "session/list"
SESSION_CLOSE = "session/close"
SESSION_DELETE = "session/delete"
SESSION_FORK = "session/fork"
SESSION_PROMPT = "session/prompt"
SESSION_CANCEL = "session/cancel"
SESSION_SET_MODE = "session/set_mode"
SESSION_SET_MODEL = "session/set_model"
AUTHENTICATE = "authenticate"
LOGOUT = "logout"
SESSION_UPDATE = "session/update"
SESSION_REQUEST_PERMISSION = "session/request_permission"
ELICITATION_CREATE = "elicitation/create"


# ===================================================================== 权限选项命名空间
# optionId 字面量是构造端与解析端的共同真源。
APPROVE_ONCE_OPTION_ID = "approve_once"
APPROVE_ALWAYS_OPTION_ID = "approve_always"
REJECT_OPTION_ID = "reject"
PLAN_APPROVE_OPTION_ID = "plan_approve"
PLAN_REVISE_OPTION_ID = "plan_revise"
PLAN_REJECT_AND_EXIT_OPTION_ID = "plan_reject_and_exit"


# ===================================================================== AcpConn 协议
@runtime_checkable
class AcpConn(Protocol):
    """server -> client 的出站通道（被 AcpSession / AcpInteractionBridge 消费）。

    实际实现就是 :class:`qingxiaotuan.kernel.acp.server.AcpServer` 自身：它既读入
    客户端消息，也把 ``session/update`` 通知与 ``session/request_permission`` 反向 RPC
    写回 stdout。``conn`` 这个名字刻意贴近 TS 的 ``AcpClient``。
    """

    async def session_update(self, notification: Dict[str, Any]) -> None:
        """发送一条 ``session/update`` 通知。"""
        ...

    async def request_permission(
        self, *, session_id: str, options: List[Dict[str, Any]], tool_call: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发起 ``session/request_permission`` 反向 RPC，阻塞等待客户端响应。"""
        ...


# ===================================================================== 结果/响应类型
@dataclass
class ApprovalResponse:
    """引擎 ApprovalResponse：decision + 可选 scope / selectedLabel。

    自研实现 —— 审批响应（用 dataclass 建模）。
    """

    decision: str  # "approved" | "rejected" | "cancelled"
    scope: Optional[str] = None  # "session" 时表示 approve_always
    selected_label: Optional[str] = None


@dataclass
class RequestPermissionResponse:
    """客户端回的 ``RequestPermissionResponse``：outcome 决定映射。"""

    outcome: str  # "selected" | "cancelled"
    option_id: Optional[str] = None


# ===================================================================== SessionUpdate 构造器
def _wrap(session_id: str, **update: Any) -> Dict[str, Any]:
    return {"sessionId": session_id, "update": update}


def agent_message_chunk(session_id: str, text: str) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="agent_message_chunk",
                 content={"type": "text", "text": text})


def agent_thought_chunk(session_id: str, text: str) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="agent_thought_chunk",
                 content={"type": "text", "text": text})


def tool_call(
    session_id: str,
    tool_call_id: str,
    *,
    title: str,
    kind: str = "other",
    status: str = "in_progress",
    raw_input: Any = None,
    content: Optional[List[Dict[str, Any]]] = None,
    locations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    upd: Dict[str, Any] = {
        "sessionUpdate": "tool_call",
        "toolCallId": tool_call_id,
        "title": title,
        "kind": kind,
        "status": status,
    }
    if raw_input is not None:
        upd["rawInput"] = raw_input
    if content is not None:
        upd["content"] = content
    if locations is not None:
        upd["locations"] = locations
    return _wrap(session_id, **upd)


def tool_call_update(
    session_id: str,
    tool_call_id: str,
    *,
    status: Optional[str] = None,
    title: Optional[str] = None,
    kind: Optional[str] = None,
    raw_input: Any = None,
    raw_output: Any = None,
    content: Optional[List[Dict[str, Any]]] = None,
    locations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    upd: Dict[str, Any] = {"sessionUpdate": "tool_call_update", "toolCallId": tool_call_id}
    if status is not None:
        upd["status"] = status
    if title is not None:
        upd["title"] = title
    if kind is not None:
        upd["kind"] = kind
    if raw_input is not None:
        upd["rawInput"] = raw_input
    if raw_output is not None:
        upd["rawOutput"] = raw_output
    if content is not None:
        upd["content"] = content
    if locations is not None:
        upd["locations"] = locations
    return _wrap(session_id, **upd)


def available_commands_update(session_id: str, commands: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="available_commands_update",
                 availableCommands=list(commands))


def current_mode_update(session_id: str, current_mode_id: str) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="current_mode_update", currentModeId=current_mode_id)


def config_option_update(session_id: str, config_options: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="config_option_update",
                 configOptions=list(config_options))


def usage_update(session_id: str, used: int, size: int) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="usage_update", used=used, size=size)


def session_info_update(session_id: str, title: Optional[str]) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="session_info_update", title=title)


def plan(session_id: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="plan", entries=list(entries))


def plan_update(session_id: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="plan_update", entries=list(entries))


def plan_removed(session_id: str) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="plan_removed")


def user_message_chunk(session_id: str, text: str) -> Dict[str, Any]:
    return _wrap(session_id, sessionUpdate="user_message_chunk",
                 content={"type": "text", "text": text})


# ===================================================================== 顶层便捷构造器
def session_update(session_id: str, **body: Any) -> Dict[str, Any]:
    """构造一条完整的 ``session/update`` 通知（**body** 即 ``update`` 内层字段）。"""
    return _wrap(session_id, **body)


def request_permission(
    session_id: str, options: List[Dict[str, Any]], tool_call: Dict[str, Any]
) -> Dict[str, Any]:
    """构造 ``session/request_permission`` 反向 RPC 的参数体（供 conn.request_permission 使用）。"""
    return {"sessionId": session_id, "options": list(options), "toolCall": tool_call}
