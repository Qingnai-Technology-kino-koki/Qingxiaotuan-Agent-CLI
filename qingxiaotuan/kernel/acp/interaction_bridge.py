"""ACP 交互桥：把引擎的阻塞式 approval（人审）转成 ``session/request_permission`` 反向 RPC。

自研实现 —— 交互桥（审批/会话消息转 kernel 事件）（精简版，
仅保留 approval 桥；question / elicitation 在精简版标 TODO，不影响主链路）。

纯映射函数（approval_request_to_permission_options / permission_response_to_approval_response
/ build_permission_tool_call_update）无 IO，可单测；:class:`AcpInteractionBridge` 只负责
发起 ``conn.request_permission`` 并把响应映射回引擎 ApprovalResponse。RPC 失败一律兜底为
``rejected``（拒绝比放行更安全）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .events_map import acp_tool_call_id
from .protocol import (
    APPROVE_ALWAYS_OPTION_ID,
    APPROVE_ONCE_OPTION_ID,
    AcpConn,
    ApprovalResponse,
    PLAN_APPROVE_OPTION_ID,
    PLAN_REJECT_AND_EXIT_OPTION_ID,
    PLAN_REVISE_OPTION_ID,
    REJECT_OPTION_ID,
    RequestPermissionResponse,
)


# ===================================================================== 纯映射
def approval_request_to_permission_options(req: Dict[str, Any]) -> List[Dict[str, Any]]:
    """构造下发客户端的 PermissionOption[]。

    plan_review 场景扩展为 ``plan_opt_<i>`` / ``plan_approve`` / ``plan_revise`` /
    ``plan_reject_and_exit``；其它场景返回标准三选项（approve_once / approve_always / reject）。
    """
    display = req.get("display") or {}
    if display.get("kind") == "plan_review":
        options = display.get("options") or []
        if isinstance(options, list) and len(options) >= 2:
            approve = [
                {"optionId": f"plan_opt_{i}", "name": opt.get("label", f"Option {i}"),
                 "kind": "allow_once"}
                for i, opt in enumerate(options)
            ]
        else:
            approve = [{"optionId": PLAN_APPROVE_OPTION_ID, "name": "Approve", "kind": "allow_once"}]
        return [
            *approve,
            {"optionId": PLAN_REVISE_OPTION_ID, "name": "Revise", "kind": "reject_once"},
            {"optionId": PLAN_REJECT_AND_EXIT_OPTION_ID, "name": "Reject and Exit", "kind": "reject_once"},
        ]
    return [
        {"optionId": APPROVE_ONCE_OPTION_ID, "name": "Approve once", "kind": "allow_once"},
        {"optionId": APPROVE_ALWAYS_OPTION_ID, "name": "Approve for this session", "kind": "allow_always"},
        {"optionId": REJECT_OPTION_ID, "name": "Reject", "kind": "reject_once"},
    ]


def permission_response_to_approval_response(
    req: Dict[str, Any], response: RequestPermissionResponse,
) -> ApprovalResponse:
    """客户端响应 -> 引擎 ApprovalResponse。"""
    if response.outcome == "cancelled":
        return ApprovalResponse(decision="cancelled")
    option_id = response.option_id
    display = req.get("display") or {}
    if display.get("kind") == "plan_review":
        return _map_plan_review_option(display, option_id)
    if option_id in (APPROVE_ONCE_OPTION_ID, "approve"):
        return ApprovalResponse(decision="approved")
    if option_id in (APPROVE_ALWAYS_OPTION_ID, "approve_for_session"):
        return ApprovalResponse(decision="approved", scope="session")
    if option_id == REJECT_OPTION_ID:
        return ApprovalResponse(decision="rejected")
    # 未知 optionId：防御性 reject（比误批准安全）。
    return ApprovalResponse(decision="rejected")


def build_permission_tool_call_update(req: Dict[str, Any]) -> Dict[str, Any]:
    """构造把 permission 请求关联到具体 tool call 的 ToolCallUpdate。

    toolCallId 用前缀线缆 id ``${turnId}:${rawId}``，与所有 tool_call 通知一致，
    便于客户端把审批提示关联到已渲染的 tool card。
    """
    raw_id = req.get("toolCallId") or req.get("toolName") or "approval"
    turn_id = req.get("turnId")
    tool_call_id = acp_tool_call_id(turn_id, raw_id) if isinstance(turn_id, int) else raw_id
    content: List[Dict[str, Any]] = []
    headline = _display_block_to_acp_content(req.get("display"))
    if headline is not None:
        content.append(headline)
    content.append({
        "type": "content",
        "content": {"type": "text", "text": f"Requesting approval to {req.get('action', 'run tool')}"},
    })
    return {"toolCallId": tool_call_id, "title": req.get("toolName", "tool"), "content": content}


# ===================================================================== 桥
class AcpInteractionBridge:
    """把引擎 approval 经 ``session/request_permission`` 转给 client 并回收决策。"""

    def __init__(self, conn: AcpConn, session_id: str, elicitation_form: bool = False) -> None:
        self._conn = conn
        self._session_id = session_id
        self._elicitation_form = elicitation_form
        self._in_flight: set = set()

    async def handle_approval(self, req: Dict[str, Any]) -> ApprovalResponse:
        """桥一个 ApprovalRequest；RPC 失败 -> rejected。"""
        tool_call = build_permission_tool_call_update(req)
        options = approval_request_to_permission_options(req)
        try:
            resp = await self._conn.request_permission(
                session_id=self._session_id, options=options, tool_call=tool_call
            )
            parsed = _parse_response(resp)
            return permission_response_to_approval_response(req, parsed)
        except Exception:
            # 拒绝比放行更安全
            return ApprovalResponse(decision="rejected")


# ===================================================================== 内部辅助
def _parse_response(resp: Dict[str, Any]) -> RequestPermissionResponse:
    outcome = (resp or {}).get("outcome") or {}
    return RequestPermissionResponse(
        outcome=outcome.get("outcome", "cancelled"),
        option_id=outcome.get("optionId"),
    )


def _map_plan_review_option(display: Dict[str, Any], option_id: str) -> ApprovalResponse:
    if option_id == PLAN_APPROVE_OPTION_ID:
        return ApprovalResponse(decision="approved")
    if option_id == PLAN_REVISE_OPTION_ID:
        return ApprovalResponse(decision="rejected", selected_label="Revise")
    if option_id == PLAN_REJECT_AND_EXIT_OPTION_ID:
        return ApprovalResponse(decision="rejected", selected_label="Reject and Exit")
    import re

    m = re.match(r"^plan_opt_(\d+)$", option_id or "")
    if m:
        i = int(m.group(1))
        options = display.get("options") or []
        if isinstance(options, list) and 0 <= i < len(options):
            return ApprovalResponse(decision="approved", selected_label=options[i].get("label"))
        return ApprovalResponse(decision="rejected")
    return ApprovalResponse(decision="rejected")


def _display_block_to_acp_content(display: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(display, dict):
        return None
    kind = display.get("kind")
    if kind in ("diff", "file_io") and "before" in display and "after" in display:
        return {
            "type": "diff", "path": display.get("path"),
            "oldText": display.get("before"), "newText": display.get("after"),
        }
    if kind == "plan_review":
        text = display.get("plan", "")
        if not text:
            return None
        if display.get("path"):
            text = f"Plan saved to: {display['path']}\n\n{text}"
        return {"type": "content", "content": {"type": "text", "text": text}}
    return None
