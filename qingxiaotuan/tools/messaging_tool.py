"""跨会话消息工具 (对标 Claude Code 2.1 SendMessage / ListAgents)。

Claude Code 2.1.239 新增 Windows 跨会话消息:
- SendMessage: 从当前会话向另一个会话发送消息
- ListAgents: 列出当前活跃的会话/代理

青小团实现:
- 基于文件的消息总线 (core/message_bus.py)
- 本地多进程可用, 支持 Windows/macOS/Linux
- 消息自动过期, 会话自动清理
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from ..core.kernel import Kernel, Plugin
from ..core.message_bus import get_message_bus
from ..tools.base import Tool, ToolContext, string_prop


def _send_message(ctx: ToolContext, to: str, message: str) -> str:
    """向另一个会话发送消息。

    Args:
        to: 目标会话 ID (通过 list_agents 获取)
        message: 消息内容
    """
    bus = get_message_bus()

    # 确保当前会话已注册
    session_id = ctx.config("_session_id", "")
    if not session_id:
        session_id = uuid.uuid4().hex[:8]
        ctx.kernel.provide("_session_id", session_id)
        bus.register(session_id, meta={"workspace": ctx.workspace})

    # 查找目标会话
    agents = bus.list_agents(include_self=False)
    target_ids = {a["session_id"] for a in agents}
    if to not in target_ids:
        available = ", ".join(sorted(target_ids)) if target_ids else "(无其他活跃会话)"
        return f"[错误] 目标会话 '{to}' 不在活跃列表中。当前活跃会话: {available}"

    msg_id = bus.send(session_id, to, message)
    if msg_id:
        return f"消息已发送至会话 {to} (id={msg_id})"
    return f"[错误] 发送失败"


def _receive_messages(ctx: ToolContext) -> str:
    """读取当前会话的所有未读消息。"""
    bus = get_message_bus()

    session_id = ctx.config("_session_id", "")
    if not session_id:
        session_id = uuid.uuid4().hex[:8]
        ctx.kernel.provide("_session_id", session_id)
        bus.register(session_id, meta={"workspace": ctx.workspace})

    messages = bus.receive(session_id)
    if not messages:
        return "没有新消息"

    lines = []
    for msg in messages:
        lines.append(
            f"来自 {msg.from_session} ({int(msg.ts)}s ago):\n  {msg.content}"
        )
    return f"{len(messages)} 条新消息:\n" + "\n\n".join(lines)


def _list_agents(ctx: ToolContext) -> str:
    """列出当前活跃的会话/代理。"""
    bus = get_message_bus()

    session_id = ctx.config("_session_id", "")
    if not session_id:
        session_id = uuid.uuid4().hex[:8]
        ctx.kernel.provide("_session_id", session_id)
        bus.register(session_id, meta={"workspace": ctx.workspace})
    else:
        bus.heartbeat(session_id)

    agents = bus.list_agents(include_self=False, session_id=session_id)
    if not agents:
        return "当前没有其他活跃会话"

    lines = ["活跃会话:"]
    for a in agents:
        meta = a.get("meta", {})
        ws = meta.get("workspace", "?")
        lines.append(
            f"  - {a['session_id']}  pid={a['pid']}  "
            f"uptime={a['uptime']}s  workspace={ws}"
        )
    lines.append(f"\n使用 send_message(to='{agents[0]['session_id']}', message='...') 发送消息")
    return "\n".join(lines)


class MessagingPlugin(Plugin):
    name = "tools.messaging"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("tools.messaging.enabled", True):
            return

        registry = kernel.require("tool_registry")

        registry.register(Tool(
            name="send_message",
            description=(
                "向另一个活跃的代理会话发送消息。"
                "先用 list_agents 查看可用会话 ID。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "to": string_prop("目标会话 ID"),
                    "message": string_prop("消息内容"),
                },
                "required": ["to", "message"],
            },
            handler=_send_message,
            group="messaging",
            read_only=True,
        ))

        registry.register(Tool(
            name="receive_messages",
            description="读取当前会话的所有未读跨会话消息",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_receive_messages,
            group="messaging",
            read_only=True,
        ))

        registry.register(Tool(
            name="list_agents",
            description="列出当前所有活跃的代理会话 (ID、PID、工作区)",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_list_agents,
            group="messaging",
            read_only=True,
        ))
