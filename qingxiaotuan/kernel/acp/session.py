"""ACP session：把一个 prompt 驱动成一组 ``session/update`` 通知 + 一个 PromptResponse。

自研实现 —— ACP 会话管理（去掉 klient/
scope/compaction/terminal 等重型依赖，保留驱动流的语义核心）。

设计：
- ``AcpSession`` 不持有 agent；agent 经 DIP 注入的 ``agent_provider`` 提供
  ``prompt(session_id, input_text, signal)``（返回异步事件源）与 ``cancel(...)``。
- ``prompt(blocks)``：ACP ContentBlock[] -> kernel ContentPart[] -> 文本 -> 驱动
  ``agent_provider.prompt`` 的异步事件迭代；把 agent 运行期事件经 events_map 翻译成
  ``session/update`` 通知发给 client；``turn.ended`` 结算 PromptResponse（stopReason 映射）。
- tool_call 懒创建规则由 :class:`~qingxiaotuan.kernel.acp.events_map.ToolCallRegistry` 落实。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from ..contract import ContentPart
from .convert import acp_blocks_to_content_parts
from .events_map import (
    ToolCallRegistry,
    assistant_delta_to_session_update,
    thinking_delta_to_session_update,
    tool_progress_to_session_update,
    tool_result_to_session_update,
    turn_end_reason_to_stop_reason,
)
from .protocol import AcpConn, agent_message_chunk, available_commands_update


class AgentProvider:
    """agent_provider 接口（结构化鸭子类型，仅文档/提示用）。

    实现需提供：
    - ``async prompt(self, session_id, input_text, signal) -> AsyncIterator[dict]``
      迭代产出 engine 事件（见 events_map 模块 docstring 的形状）。
    - ``cancel(self, session_id, turn_id) -> None``：取消指定 turn。
    - 可选 ``available_commands(self) -> List[dict]``。
    """


class AcpSession:
    """一个 ACP session 的 prompt 驱动 + 事件翻译。"""

    def __init__(
        self,
        conn: AcpConn,
        session_id: str,
        agent_provider: Any,
        *,
        elicitation_form: bool = False,
    ) -> None:
        self._conn = conn
        self._session_id = session_id
        self._agent_provider = agent_provider
        self._elicitation_form = elicitation_form

        self._turn_counter = 0
        self._current_turn_id: Optional[int] = None
        self._registry: Optional[ToolCallRegistry] = None
        self._cancel_signal: Optional[asyncio.Event] = None
        self._disposed = False

    # -------------------------------------------------------------- 命令/Skill
    @staticmethod
    def detect_slash_intent(text: str, command_names: set) -> Optional[str]:
        """极简 slash 意图检测：以 ``/`` 开头且首词命中已知命令名则取其名。

        返回命令名或 None（非 slash / 未知命令）。未知命令由调用方本地回显
        ``Unknown ACP command`` 提示，不进模型。
        """
        if not text.startswith("/"):
            return None
        name = text[1:].split(None, 1)[0] if text[1:].split(None, 1) else ""
        return name if name in command_names else None

    # -------------------------------------------------------------- 可用命令
    def available_commands(self) -> List[Dict[str, Any]]:
        fn = getattr(self._agent_provider, "available_commands", None)
        if callable(fn):
            try:
                return list(fn())
            except Exception:
                return []
        return []

    async def emit_available_commands_update(self) -> None:
        try:
            await self._conn.session_update(
                available_commands_update(self._session_id, self.available_commands())
            )
        except Exception:
            # 通知失败不应中断主链路
            pass

    # -------------------------------------------------------------- prompt 驱动
    async def prompt(self, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """驱动一次 prompt，返回 ``{"stopReason": ...}``。

        过程中通过 ``conn.session_update`` 流式下发通知；``turn.ended`` 结算 stopReason
        （end_turn / cancelled / refusal）。
        """
        parts: List[ContentPart] = acp_blocks_to_content_parts(blocks)
        text = "".join(p.text or "" for p in parts if p.type == "text")

        # slash 意图检测（本地处理，不进模型）
        intent = self.detect_slash_intent(text, {c.get("name", "") for c in self.available_commands()})
        if intent is not None and intent not in {"help"}:
            await self._emit(
                agent_message_chunk(self._session_id, f"Unknown ACP command: /{intent}. Use /help to see available commands.")
            )
            return {"stopReason": "end_turn"}
        if intent == "help":
            await self._emit(agent_message_chunk(self._session_id, "Available commands: " + ", ".join(c.get("name", "") for c in self.available_commands())))
            return {"stopReason": "end_turn"}

        turn_id = self._next_turn_id()
        self._current_turn_id = turn_id
        self._registry = ToolCallRegistry(self._session_id)
        self._cancel_signal = asyncio.Event()
        response: Dict[str, Any] = {"stopReason": "end_turn"}

        try:
            agen: AsyncIterator[Dict[str, Any]] = self._agent_provider.prompt(
                self._session_id, text, self._cancel_signal
            )
            async for ev in agen:
                settled = await self._dispatch(ev, turn_id)
                if settled is not None:
                    response = settled
        except asyncio.CancelledError:
            response = {"stopReason": "cancelled"}
        except Exception as exc:  # noqa: BLE001
            # ACP 不鼓励经 stopReason 表达错误；回显一段本地说明并优雅 end_turn。
            await self._emit(agent_message_chunk(self._session_id, f"[agent error] {exc}"))
            response = {"stopReason": "end_turn"}

        self._current_turn_id = None
        self._registry = None
        return response

    # -------------------------------------------------------------- 事件分发
    async def _dispatch(self, ev: Dict[str, Any], turn_id: int) -> Optional[Dict[str, Any]]:
        et = ev.get("type")
        reg = self._registry
        if et == "assistant.delta":
            await self._emit(assistant_delta_to_session_update(self._session_id, ev.get("delta", "")))
            return None
        if et == "thinking.delta":
            await self._emit(thinking_delta_to_session_update(self._session_id, ev.get("delta", "")))
            return None
        if et == "tool.call.delta":
            if reg is not None:
                await self._emit(
                    reg.handle_delta(
                        turn_id, ev["tool_call_id"], name=ev.get("name"),
                        arguments_part=ev.get("arguments_part", ""),
                    )
                )
            return None
        if et == "tool.call.started":
            if reg is not None:
                await self._emit(
                    reg.handle_started(
                        turn_id, ev["tool_call_id"], name=ev["name"], args=ev.get("args"),
                        description=ev.get("description"), display=ev.get("display"),
                    )
                )
            return None
        if et == "tool.progress":
            upd = tool_progress_to_session_update(
                self._session_id, turn_id, ev.get("tool_call_id", ""),
                (ev.get("update") or {}).get("text"),
            )
            if upd is not None:
                await self._emit(upd)
            return None
        if et == "tool.result":
            loc = reg.locations_for(turn_id, ev.get("tool_call_id", "")) if reg else None
            await self._emit(
                tool_result_to_session_update(
                    self._session_id, turn_id, ev.get("tool_call_id", ""),
                    ev.get("output"), bool(ev.get("is_error")), locations=loc,
                )
            )
            if reg is not None:
                reg.clear(turn_id, ev.get("tool_call_id", ""))
            return None
        if et == "turn.ended":
            return {"stopReason": turn_end_reason_to_stop_reason(ev.get("reason", "completed"), ev.get("error"))}
        # 其它事件（compaction.* 等）在精简版忽略。
        return None

    async def _emit(self, notification: Dict[str, Any]) -> None:
        """同步写出一条 ``session/update`` 通知（inline，保证通知先于结算响应到达）。"""
        if notification is None:
            return
        try:
            await self._conn.session_update(notification)
        except Exception:
            # 通知失败不应中断驱动流
            pass

    # -------------------------------------------------------------- 取消/清理
    def cancel(self) -> None:
        """取消当前在跑的 turn（置信号 + 调 agent_provider.cancel）。"""
        if self._cancel_signal is not None:
            self._cancel_signal.set()
        if self._current_turn_id is not None:
            try:
                self._agent_provider.cancel(self._session_id, self._current_turn_id)
            except Exception:
                pass

    def dispose(self) -> None:
        self._disposed = True
        self.cancel()

    def _next_turn_id(self) -> int:
        self._turn_counter += 1
        return self._turn_counter
