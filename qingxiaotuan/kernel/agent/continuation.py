"""续转服务 —— 一轮 step 结束后自动发起下一轮。

在 on_did_finish_step hook 里：若 finish_reason == 'tool_calls' 且非 stopTurn，
自动入队 ContinuationStepRequest，令 loop 继续下一轮（ReAct 续转）。
"""

from __future__ import annotations

from ..contract import FinishReason
from .loop import AfterStepContext, AgentLoopService
from .step_request import ContinuationStepRequest


class LoopContinuationService:
    """包装 AgentLoopService，注入续转 hook。"""

    def __init__(self, loop_service: AgentLoopService) -> None:
        self._loop = loop_service
        self._disposable = loop_service.hooks.on_did_finish_step.register(
            "loop-continuation",
            self._on_did_finish_step,
        )

    async def _on_did_finish_step(self, ctx: AfterStepContext) -> None:
        if ctx.stop_turn or ctx.finish_reason != FinishReason.TOOL_CALLS:
            return
        self._loop.enqueue(ContinuationStepRequest())

    def dispose(self) -> None:
        self._disposable.dispose()
