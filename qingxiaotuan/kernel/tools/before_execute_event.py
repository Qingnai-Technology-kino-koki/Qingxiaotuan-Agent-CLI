"""before_execute_event —— 工具执行前的广播事件语义。

事件在「真正执行工具之前」被广播给所有订阅者（权限闸门即其一）。订阅者可以：
- ``veto(result)``            直接否决（工具不执行，用给定 result 作为结果）
- ``allow()``                 显式放行（一旦 allow，后续订阅者不再处理，整体不否决）
- ``pass(metadata)``         放行并可附带元数据
- ``wait_until(factory)``    注册一个「待决否决工厂」：阻塞等到裁决完成再决定，
                             用于需要人工/异步裁决的 ask 场景（如权限闸门在 policy=ask 时）

核心用 asyncio.Future 实现「阻塞等裁决」：gate 的 waitUntil 工厂在事件被 fire 后才
被 await，其返回的 ``BeforeExecuteDecision`` 若含 veto 则最终否决。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional

from .contract import (
    ExecutableTool,
    ExecutableToolResult,
    RunnableToolExecution,
    ToolCall,
)


@dataclass
class BeforeExecuteDecision:
    """waitUntil 工厂的返回：含 veto 则否决，否则视为通过。"""

    veto: Optional[ExecutableToolResult] = None
    execution_metadata: Any = None


class BeforeToolExecuteEvent:
    """执行前事件，承载上下文与裁决状态。"""

    def __init__(
        self,
        *,
        turn_id: int,
        signal: Any,
        trace: Any,
        tool_call: ToolCall,
        tool_calls: List[ToolCall],
        tool: Optional[ExecutableTool],
        args: Any,
        execution: RunnableToolExecution,
    ) -> None:
        self.turn_id = turn_id
        self.signal = signal
        self.trace = trace
        self.tool_call = tool_call
        self.tool_calls = tool_calls
        self.tool = tool
        self.args = args
        self.execution = execution

        self._veto_result: Optional[ExecutableToolResult] = None
        self._allowed = False
        self._pass_metadata: Any = None
        self._pending_vetos: List[Callable[[], Awaitable[Optional[BeforeExecuteDecision]]]] = []
        self._open = True  # 只允许在 fire 阶段同步调用裁决方法

    # ---- 裁决 API（订阅者调用）---------------------------------------------

    def veto(self, result: ExecutableToolResult) -> None:
        self._assert_open("veto")
        self._veto_result = self._veto_result or result

    def allow(self) -> None:
        self._assert_open("allow")
        self._allowed = True

    def pass_metadata(self, metadata: Any = None) -> None:
        self._assert_open("pass")
        if self._pass_metadata is None:
            self._pass_metadata = metadata

    def wait_until(
        self, factory: Callable[[], Awaitable[Optional[BeforeExecuteDecision]]]
    ) -> None:
        self._assert_open("waitUntil")
        self._pending_vetos.append(factory)

    # ---- 状态读取 ----------------------------------------------------------

    @property
    def veto_result(self) -> Optional[ExecutableToolResult]:
        return self._veto_result

    @property
    def allowed(self) -> bool:
        return self._allowed

    @property
    def pass_metadata(self) -> Any:
        return self._pass_metadata

    @property
    def pending_vetos(self) -> List[Callable[[], Awaitable[Optional[BeforeExecuteDecision]]]]:
        return self._pending_vetos

    def close_registration(self) -> None:
        """fire 阶段结束后调用：此后任何裁决调用都会报错（对齐 TS 的 assertOpen）。"""
        self._open = False

    # ---- 驱动 --------------------------------------------------------------

    async def fire(self, subscribers: List[Callable[["BeforeToolExecuteEvent"], Awaitable[None]]]):
        """依次通知订阅者，并汇总裁决。

        返回最终的 ``BeforeExecuteDecision``：有 veto 则带 veto；否则 None（放行）。
        """
        for cb in subscribers:
            await cb(self)
            if self._allowed:
                self.close_registration()
                return None
            if self._veto_result is not None:
                self.close_registration()
                return BeforeExecuteDecision(veto=self._veto_result)

        for factory in self._pending_vetos:
            decision = await factory()
            if decision is not None and decision.veto is not None:
                self.close_registration()
                return decision

        self.close_registration()
        return None

    def _assert_open(self, statement: str) -> None:
        if not self._open:
            raise RuntimeError(f"{statement} 不能在异步阶段调用")


__all__ = ["BeforeExecuteDecision", "BeforeToolExecuteEvent"]
