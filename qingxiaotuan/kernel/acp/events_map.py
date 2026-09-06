"""引擎/本地事件 -> ACP ``session/update`` 的纯映射 + tool_call 懒创建规则。

自研实现 —— 事件映射。所有函数保持纯（不 IO），
便于单测。关键规则见 :class:`ToolCallRegistry` 与 :func:`acp_tool_call_id`。

引擎事件模型（由 ``agent_provider`` 异步迭代产出，dict 形态）：
- ``{"type": "assistant.delta", "delta": <str>}``
- ``{"type": "thinking.delta", "delta": <str>}``
- ``{"type": "tool.call.delta", "turn_id": int, "tool_call_id": str,
   "name": str|None, "arguments_part": str}``
- ``{"type": "tool.call.started", "turn_id": int, "tool_call_id": str,
   "name": str, "args": <any>, "description": str|None, "display": <any>|None}``
- ``{"type": "tool.progress", "turn_id": int, "tool_call_id": str,
   "update": {"kind": "status", "text": str}}``
- ``{"type": "tool.result", "turn_id": int, "tool_call_id": str,
   "output": <any>, "is_error": bool}``
- ``{"type": "turn.ended", "reason": <str>, "error": {"code": str}|None}``
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .convert import tool_result_to_acp
from .protocol import (
    agent_message_chunk,
    agent_thought_chunk,
    plan,
    tool_call,
    tool_call_update,
    usage_update,
)


# ===================================================================== 基础工具
def acp_tool_call_id(turn_id: int, raw_id: str) -> str:
    """ACP 线缆 toolCallId = ``${turnId}:${rawId}``。

    同一 session 内多次 turn 可能复用模型分配的 raw id（模型重试时），加 turn 前缀
    避免碰撞。raw id 仍是进程内累加器的键，仅线缆 id 加前缀。
    """
    return f"{turn_id}:{raw_id}"


def stringify_args(args: Any) -> str:
    """工具参数 JSON 化，永不抛（流式推送不能因序列化崩溃）。"""
    try:
        return json.dumps(args, ensure_ascii=False) if args is not None else ""
    except (TypeError, ValueError):
        return str(args)


def infer_tool_kind(name: str) -> str:
    """从工具名启发式推断 ACP ToolKind（未知 -> ``other``）。"""
    return {
        "Read": "read", "Glob": "read", "Grep": "read",
        "Write": "edit", "Edit": "edit",
        "Bash": "execute", "Terminal": "execute",
        "WebFetch": "fetch", "WebSearch": "fetch",
        "Think": "think",
    }.get(name, "other")


# ===================================================================== 纯映射函数
def assistant_delta_to_session_update(session_id: str, delta: str) -> Dict[str, Any]:
    return agent_message_chunk(session_id, delta)


def thinking_delta_to_session_update(session_id: str, delta: str) -> Dict[str, Any]:
    return agent_thought_chunk(session_id, delta)


def tool_call_start_to_session_update(
    session_id: str, turn_id: int, raw_id: str, *, name: str,
    args: Any, description: Optional[str] = None, display: Any = None,
) -> Dict[str, Any]:
    """``tool.call.started`` 的 CREATE（当此前没有 delta 懒创建过）。"""
    """``tool.call.started`` 的 CREATE（当此前没有 delta 懒创建过）。"""
    content = [{"type": "content", "content": {"type": "text", "text": stringify_args(args)}}]
    diff = _display_block_to_acp_content(display)
    if diff is not None:
        content.insert(0, diff)
    return tool_call(
        session_id,
        acp_tool_call_id(turn_id, raw_id),
        title=description or name,
        kind=infer_tool_kind(name),
        status="in_progress",
        raw_input=args,
        content=content,
        locations=_tool_call_locations(name, args, display),
    )


def tool_call_lazy_create(
    session_id: str, turn_id: int, raw_id: str, *, name: Optional[str] = None,
    arguments_part: str = "",
) -> Dict[str, Any]:
    """首个 ``tool.call.delta`` 到达时懒创建 pending 的 tool_call (CREATE)。

    引擎在 provider 流式阶段先发 ``tool.call.delta``、随后才发 ``tool.call.started``
    （调用真正派发时）。从首个 delta 懒创建，让后续 delta 有合法父节点可 UPDATE，
    客户端不会在 started 到达前看到 “tool call not found”。
    """
    nm = name or "tool"
    return tool_call(
        session_id,
        acp_tool_call_id(turn_id, raw_id),
        title=nm,
        kind=infer_tool_kind(nm) if name else "other",
        status="pending",
        content=[{"type": "content", "content": {"type": "text", "text": arguments_part}}],
    )


def tool_call_started_upgrade(
    session_id: str, turn_id: int, raw_id: str, *, name: str,
    args: Any, description: Optional[str] = None, display: Any = None,
) -> Dict[str, Any]:
    """``tool.call.started`` 到达、且已懒创建过时，发 UPDATE 升级（绝不第二个 CREATE）。

    把 started 携带的完整 metadata（title/kind/rawInput/locations/diff）落到已存在的
    card 上，并翻转 status 到 ``in_progress``。
    """
    content = [{"type": "content", "content": {"type": "text", "text": stringify_args(args)}}]
    diff = _display_block_to_acp_content(display)
    if diff is not None:
        content.insert(0, diff)
    return tool_call_update(
        session_id,
        acp_tool_call_id(turn_id, raw_id),
        status="in_progress",
        title=description or name,
        kind=infer_tool_kind(name),
        raw_input=args,
        content=content,
        locations=_tool_call_locations(name, args, display),
    )


def tool_call_delta_update(
    session_id: str, turn_id: int, raw_id: str, cumulative_args: str,
) -> Dict[str, Any]:
    """流式 args delta 的 REPLACE 内容 UPDATE（cumulative_args 为累加后的全量参数串）。"""
    return tool_call_update(
        session_id,
        acp_tool_call_id(turn_id, raw_id),
        status="in_progress",
        content=[{"type": "content", "content": {"type": "text", "text": cumulative_args}}],
    )


def tool_progress_to_session_update(
    session_id: str, turn_id: int, raw_id: str, text: Optional[str],
) -> Optional[Dict[str, Any]]:
    """仅 ``status`` 类且带文本的 progress 才发（刷新 tool card 标题），其余返回 None。"""
    if not text:
        return None
    return tool_call_update(session_id, acp_tool_call_id(turn_id, raw_id), title=text)


def tool_result_to_session_update(
    session_id: str, turn_id: int, raw_id: str, output: Any, is_error: bool,
    locations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """终态 ``tool_call_update``：status 翻转 completed/failed，内容替换为最终输出。"""
    return tool_call_update(
        session_id,
        acp_tool_call_id(turn_id, raw_id),
        status="failed" if is_error else "completed",
        content=tool_result_to_acp(output),
        raw_output=output,
        locations=locations,
    )


def turn_end_reason_to_stop_reason(
    reason: str, error: Optional[Dict[str, Any]] = None,
) -> str:
    """TurnEndReason -> ACP stopReason。

    completed -> end_turn；cancelled -> cancelled；failed + provider.filtered -> refusal；
    failed/blocked 其它 -> end_turn / refusal（blocked 复用 refusal 通道；ACP 无独立失败变体）。
    """
    if reason == "completed":
        return "end_turn"
    if reason == "cancelled":
        return "cancelled"
    if reason == "failed":
        if error is not None and error.get("code") == "provider.filtered":
            return "refusal"
        return "end_turn"
    if reason == "blocked":
        return "refusal"
    return "end_turn"


def usage_update_notification(session_id: str, used: int, size: int) -> Dict[str, Any]:
    return usage_update(session_id, used, size)


def plan_from_items(session_id: str, items: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """TodoList 投影为 ACP ``plan``（空 -> None）。"""
    if not items:
        return None
    entries = [
        {"content": it.get("title", ""), "priority": "medium",
         "status": _map_todo_status(it.get("status", "pending"))}
        for it in items
    ]
    return plan(session_id, entries)


# ----------------------------------------------------------------- 内部辅助
def _map_todo_status(status: str) -> str:
    return {
        "pending": "pending",
        "in_progress": "in_progress",
        "done": "completed",
        "completed": "completed",
    }.get(status, "pending")


def _display_block_to_acp_content(display: Any) -> Optional[Dict[str, Any]]:
    """ToolInputDisplay(diff/file_io/plan_review) -> ACP content 条目（极简版）。"""
    if not isinstance(display, dict):
        return None
    kind = display.get("kind")
    if kind in ("diff", "file_io") and "before" in display and "after" in display:
        return {
            "type": "diff",
            "path": display.get("path"),
            "oldText": display.get("before"),
            "newText": display.get("after"),
        }
    if kind == "plan_review":
        text = display.get("plan", "")
        if not text:
            return None
        if display.get("path"):
            text = f"Plan saved to: {display['path']}\n\n{text}"
        return {"type": "content", "content": {"type": "text", "text": text}}
    return None


def _tool_call_locations(name: str, args: Any, display: Any) -> Optional[List[Dict[str, Any]]]:
    """尽力推导 tool call 的 file location（仅绝对路径，否则 None）。极简版仅看 args.path。"""
    if not isinstance(args, dict):
        return None
    for key in ("file_path", "path"):
        value = args.get(key)
        if isinstance(value, str) and value and value.startswith("/"):
            return [{"path": value}]
    return None


# ===================================================================== 懒创建状态表
class ToolCallRegistry:
    """per-turn tool_call 状态表，落实懒创建规则，防止重复 CREATE。

    用法（在 :class:`~qingxiaotuan.kernel.acp.session.AcpSession` 中）：
    - 每个 ``tool.call.delta`` 调 :meth:`handle_delta`；
    - 每个 ``tool.call.started`` 调 :meth:`handle_started`；
    - 二者都返回要下发的 ``session/update`` 通知（dict）；首个 delta 返回 CREATE，
      started 到达时若已懒创建过则返回 UPDATE（升级），否则返回 CREATE。

    以 ACP 线缆 id（``${turnId}:${rawId}``）为状态键，保证：同一 raw id 在一个 turn 内
    至多一个 CREATE。
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        # acp_id -> {"args": str}
        self._states: Dict[str, Dict[str, str]] = {}
        # acp_id -> locations（started 时派生，result 时回挂）
        self._locations: Dict[str, List[Dict[str, Any]]] = {}

    def handle_delta(
        self, turn_id: int, raw_id: str, *, name: Optional[str] = None, arguments_part: str = "",
    ) -> Dict[str, Any]:
        key = acp_tool_call_id(turn_id, raw_id)
        if key in self._states:
            # 后续 delta：累加片段发 REPLACE 内容 UPDATE。
            self._states[key]["args"] += arguments_part or ""
            return tool_call_delta_update(self._session_id, turn_id, raw_id, self._states[key]["args"])
        # 首个 delta：懒创建 pending tool_call（CREATE）。
        self._states[key] = {"args": arguments_part or ""}
        return tool_call_lazy_create(
            self._session_id, turn_id, raw_id, name=name, arguments_part=arguments_part or ""

        )

    def handle_started(
        self, turn_id: int, raw_id: str, *, name: str, args: Any,
        description: Optional[str] = None, display: Any = None,
    ) -> Dict[str, Any]:
        key = acp_tool_call_id(turn_id, raw_id)
        lazy_created = key in self._states
        self._states[key] = {"args": stringify_args(args)}
        locations = _tool_call_locations(name, args, display)
        if locations is not None:
            self._locations[key] = locations
        if lazy_created:
            return tool_call_started_upgrade(
                self._session_id, turn_id, raw_id, name=name, args=args,
                description=description, display=display,
            )
        return tool_call_start_to_session_update(
            self._session_id, turn_id, raw_id, name=name, args=args,
            description=description, display=display,
        )

    def locations_for(self, turn_id: int, raw_id: str) -> Optional[List[Dict[str, Any]]]:
        return self._locations.get(acp_tool_call_id(turn_id, raw_id))

    def clear(self, turn_id: int, raw_id: str) -> None:
        key = acp_tool_call_id(turn_id, raw_id)
        self._states.pop(key, None)
        self._locations.pop(key, None)

    def reset(self) -> None:
        self._states.clear()
        self._locations.clear()
