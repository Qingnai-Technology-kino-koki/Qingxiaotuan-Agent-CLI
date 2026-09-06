"""桥接层 —— 把现有 Python 青小团 CLI 的 core.agent.Agent 接入 kernel AgentLoopService。

不修改 core/agent.py：仅「读取」Agent 暴露的 registry / ctx / _tool_executor，
把它们的工具执行能力包装成本端口所需的 `ToolExecutor` 协议。

提供：
- KernelAgentLoopAdapter：包裹一个 Agent，暴露 `.tool_executor`（满足 AgentLoopService 期望）。
- run_agent_loop(provider, system_prompt, tools, history, ...) 便捷函数：用 kernel.generate +
  AgentLoopService 跑完整 ReAct 循环，工具执行委托给传入的 tool_executor。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, List, Optional

from ..contract import Message, ToolCall
from .config import LoopControl
from .continuation import LoopContinuationService
from .errors import LoopError
from .loop import (
    AgentLoopService,
    LoopRunOptions,
    LoopStreamEvent,
    ToolExecutionResult,
    ToolExecutor,
)
from .step_request import ContinuationStepRequest, MessageStepRequest
from .step_retry import StepRetryService


class _AgentToolExecutor(ToolExecutor):
    """把 Agent 现有工具执行能力桥接为 kernel ToolExecutor 协议（async generator）。

    简化点：复用核心 registry.dispatch 做同步执行（不走 core ToolExecutor 的并行/
    超时批处理），每个 ToolCall 直接 yield 一个 ToolExecutionResult。真实 CLI 场景下
    够用；若需并行/超时策略可由桥接层再封装。
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent

    async def execute(
        self,
        calls: List[ToolCall],
        options: dict,
    ) -> AsyncIterator[ToolExecutionResult]:
        registry = self._agent.registry
        ctx = self._agent.ctx
        for tc in calls:
            try:
                result_text = registry.dispatch(tc.name, tc.arguments, ctx)
            except Exception as exc:  # noqa: BLE001
                result_text = f"[错误] {tc.name}: {exc}"
            yield ToolExecutionResult(tool_call_id=tc.id, output=str(result_text))


class KernelAgentLoopAdapter:
    """包裹 core.agent.Agent，暴露 AgentLoopService 期望的 tool_executor 接口。"""

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.tool_executor: ToolExecutor = _AgentToolExecutor(agent)

    async def run_agent_loop(
        self,
        provider,
        system_prompt: str,
        tools: List,
        history: List[Message],
        *,
        callbacks: Optional[Callable[[LoopStreamEvent], None]] = None,
        options: Any = None,
        loop_control: Optional[LoopControl] = None,
    ) -> Message:
        return await run_agent_loop(
            provider,
            system_prompt,
            tools,
            history,
            callbacks=callbacks,
            options=options,
            tool_executor=self.tool_executor,
            loop_control=loop_control,
        )


async def run_agent_loop(
    provider,
    system_prompt: str,
    tools: List,
    history: List[Message],
    *,
    callbacks: Optional[Callable[[LoopStreamEvent], None]] = None,
    options: Any = None,  # 预留（kernel GenerateOptions 透传位，本端口未直接使用）
    tool_executor: Optional[ToolExecutor] = None,
    loop_control: Optional[LoopControl] = None,
) -> Message:
    """便捷函数：跑完整 ReAct 循环并返回最终 assistant Message。

    约定：history 的「最后一条」视为本轮用户输入（seed），其余作为上下文；
    loop 物化时把该条用户消息回灌到上下文，与 TS 的 MessageStepRequest.turnSeed 对齐。
    """
    if tool_executor is None:
        raise ValueError("run_agent_loop 需要一个 tool_executor（工具执行器）")

    working_history = list(history)
    if working_history:
        seed = working_history[-1]
        queue_history = working_history[:-1]
    else:
        seed = None
        queue_history = []

    service = AgentLoopService(
        provider,
        system_prompt,
        tools,
        queue_history,
        tool_executor=tool_executor,
        loop_control=loop_control,
        on_event=callbacks,
    )
    LoopContinuationService(service)
    StepRetryService(service, loop_control)

    if seed is not None:
        service.enqueue(MessageStepRequest(seed))
    else:
        service.enqueue(ContinuationStepRequest())

    result = await service.run(LoopRunOptions(turn_id=1, on_event=callbacks))

    if result.type == "failed":
        raise result.error if isinstance(result.error, BaseException) else LoopError(
            "loop.failed", str(result.error)
        )
    if result.type == "cancelled":
        raise asyncio.CancelledError(str(result.reason))

    if service._final_message is None:
        raise LoopError("loop.no_response", "Loop completed without an assistant message.")
    return service._final_message
