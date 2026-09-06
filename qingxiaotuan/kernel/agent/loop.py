"""Agent Loop 主循环 —— 多步代理编排的核心执行器。

核心职责：
- 单 turn 内的 step 主循环：begin_step -> execute_step(generate + 工具执行) -> complete_step。
- 通过 StepRequestQueue 做批处理；ContinuationStepRequest 实现 tool_calls 自动续转。
- LoopErrorHandler 注册式错误恢复（step_retry 即其一）。
- max_steps 超限抛 LoopError；支持 asyncio.Event / CancelledError 取消。

说明：TS 用 DI + 多 turn job 调度，本端口收敛为「单 turn 队列 + 一个 runtime」，
去掉了 Turn/Job/Quiescence 等多 turn 编排复杂度（单轮端口中不重要，已简化）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, List, Optional

from ..contract import (
    FinishReason,
    GenerateCallbacks,
    GenerateOptions,
    Message,
    ToolCall,
    Usage,
    create_tool_message,
)
from ..generate import generate
from .config import LoopControl
from .errors import LoopError, create_max_steps_exceeded_error
from .step_queue import StepRequestBatch, StepRequestQueue
from .step_request import StepRequest, StepRequestState


# ===================================================================== 流事件

@dataclass
class LoopStreamEvent:
    """本地流式事件基类（便于将来接 UI）。"""


@dataclass
class TextEvent(LoopStreamEvent):
    delta: str


@dataclass
class ThinkingEvent(LoopStreamEvent):
    delta: str


@dataclass
class ToolCallEvent(LoopStreamEvent):
    id: str
    name: str
    arguments_delta: str = ""


def create_stream_part_handler(on_event: Callable[[LoopStreamEvent], None]):
    """把 StreamedMessagePart 转换为本地 LoopStreamEvent。

    流式消息的 type 决定如何映射成事件：
    - text_delta   -> TextEvent
    - thinking_delta -> ThinkingEvent
    - tool_call_part  -> ToolCallEvent
    """
    def handle(part) -> None:  # part: StreamedMessagePart
        if part.type == "text_delta" and part.text:
            on_event(TextEvent(delta=part.text))
        elif part.type == "thinking_delta" and part.text:
            on_event(ThinkingEvent(delta=part.text))
        elif part.type == "tool_call_part" and part.id:
            on_event(ToolCallEvent(id=part.id, name=part.name or "", arguments_delta=part.arguments_delta or ""))
    return handle


# ===================================================================== 结果类型

@dataclass
class LoopRunResult:
    type: str  # 'completed' | 'failed' | 'cancelled'
    steps: int
    truncated: bool = False
    error: Any = None
    reason: Any = None

    @staticmethod
    def completed(steps: int, truncated: bool = False) -> "LoopRunResult":
        return LoopRunResult("completed", steps, truncated)

    @staticmethod
    def failed(steps: int, error: Any) -> "LoopRunResult":
        return LoopRunResult("failed", steps, error=error)

    @staticmethod
    def cancelled(steps: int, reason: Any) -> "LoopRunResult":
        return LoopRunResult("cancelled", steps, reason=reason)


@dataclass
class LoopRunOptions:
    turn_id: int = 1
    signal: Optional[asyncio.Event] = None
    on_started: Optional[Callable[[int], None]] = None
    on_event: Optional[Callable[[LoopStreamEvent], None]] = None


# ===================================================================== 工具执行协议

@dataclass
class ToolExecutionResult:
    """一次工具执行的结果。"""
    tool_call_id: str
    output: str = ""
    is_error: bool = False
    note: str = ""
    stop_turn: bool = False


class ToolExecutor:
    """工具执行器协议（结构化 Protocol：实现类提供 execute 异步生成器）。

    执行逻辑由桥接层提供（见 bridge.py），本子系统只依赖此接口。
    """

    async def execute(
        self,
        calls: List[ToolCall],
        options: dict,
    ) -> AsyncIterator[ToolExecutionResult]:
        raise NotImplementedError
        yield  # pragma: no cover - 使函数成为 async generator


# ===================================================================== 错误恢复

@dataclass
class LoopErrorContext:
    turn_id: int
    step: Optional[int]
    step_id: Optional[str]
    signal: Any
    error: Any
    current_step: Any = None
    failed_driver: Optional[StepRequest] = None
    retry: Callable[..., Any] = lambda *a, **k: None


class LoopErrorHandler:
    """错误恢复处理器（step 级失败时接管）。

    match(ctx) -> bool 决定是否接管；handle(ctx) -> bool|None：
    - 返回真值：已恢复，循环继续（重投队列头）。
    - 返回假值/None：恢复失败，step 失败。
    """

    def __init__(
        self,
        id: str,
        match: Callable[[LoopErrorContext], bool],
        handle: Callable[[LoopErrorContext], Awaitable[Optional[bool]]],
    ) -> None:
        self.id = id
        self._match = match
        self._handle = handle

    def match(self, ctx: LoopErrorContext) -> bool:
        return self._match(ctx)

    async def handle(self, ctx: LoopErrorContext) -> Optional[bool]:
        return await self._handle(ctx)


# ===================================================================== 钩子

class _Hook:
    """极简有序钩子槽（可注册多个有序回调）。

    本端口中所有注册函数顺序执行，并可直接修改传入的 context（如 stopTurn）。
    """

    def __init__(self) -> None:
        self._handlers: List[tuple[str, Callable[[Any], Awaitable[None]]]] = []

    def register(self, id: str, fn: Callable[[Any], Awaitable[None]]):
        self._handlers.append((id, fn))
        return _Disposable(lambda: self._unregister(id))

    def _unregister(self, id: str) -> None:
        self._handlers = [(hid, fn) for hid, fn in self._handlers if hid != id]

    async def run(self, ctx: Any) -> None:
        for _, fn in self._handlers:
            await fn(ctx)


class _Disposable:
    def __init__(self, dispose: Callable[[], None]) -> None:
        self._dispose = dispose

    def dispose(self) -> None:
        self._dispose()


@dataclass
class BeforeStepContext:
    turn_id: int
    step: int
    first_step_of_turn: bool
    signal: Any


@dataclass
class AfterStepContext(BeforeStepContext):
    usage: Usage
    finish_reason: FinishReason
    stop_turn: bool = False


# ===================================================================== 主服务

@dataclass
class _StepRuntime:
    step_number: int
    batch: StepRequestBatch
    driver: StepRequest


@dataclass
class _LoopRuntime:
    turn_id: int
    signal: Any
    queue: StepRequestQueue
    steps: int = 0
    last_stop_reason: Optional[FinishReason] = None
    current: Optional[_StepRuntime] = None


@dataclass
class _StepExecutionResult:
    stop_reason: FinishReason
    hook_stop_turn: bool


class _EnqueueReceipt:
    def __init__(self, request: StepRequest, queue: StepRequestQueue) -> None:
        self._request = request
        self._queue = queue

    def abort(self, reason: Any = None) -> bool:
        return self._request.abort()


class AgentLoopService:
    """Agent 编排主循环：驱动 step 提交/执行/收敛。

    与 TS 的差异：去掉多 turn job / quiescence / 事件总线；保留
    run / enqueue / register_loop_error_handler / hooks 语义。
    """

    def __init__(
        self,
        provider,
        system_prompt: str,
        tools: List,
        history: List[Message],
        *,
        tool_executor: ToolExecutor,
        loop_control: Optional[LoopControl] = None,
        on_event: Optional[Callable[[LoopStreamEvent], None]] = None,
    ) -> None:
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = tools
        self._history: List[Message] = list(history)
        self.tool_executor = tool_executor
        self.loop_control = loop_control or LoopControl.defaults()
        self._on_event = on_event

        self._queue = StepRequestQueue()
        self._error_handlers: List[LoopErrorHandler] = []
        self._final_message: Optional[Message] = None

        self.hooks = type(
            "LoopHooks",
            (),
            {"on_will_begin_step": _Hook(), "on_did_finish_step": _Hook()},
        )()

    # ----------------------------------------------------------------- 入队

    def enqueue(self, request: StepRequest, at: str = "tail") -> _EnqueueReceipt:
        self._queue.enqueue(request, at)
        return _EnqueueReceipt(request, self._queue)

    def register_loop_error_handler(
        self,
        handler: LoopErrorHandler,
        options: Optional[dict] = None,
    ) -> _Disposable:
        # 简化排序（before/after 暂未实现，保持插入顺序）
        self._error_handlers.append(handler)
        return _Disposable(lambda: self._remove_handler(handler.id))

    def _remove_handler(self, id: str) -> None:
        self._error_handlers = [h for h in self._error_handlers if h.id != id]

    # ----------------------------------------------------------------- 运行

    async def run(self, options: Optional[LoopRunOptions] = None) -> LoopRunResult:
        opts = options or LoopRunOptions()
        signal = opts.signal or asyncio.Event()
        runtime = _LoopRuntime(
            turn_id=opts.turn_id,
            signal=signal,
            queue=self._queue,
            steps=0,
        )
        try:
            while True:
                try:
                    begun = await self._begin_step(runtime, opts)
                    if isinstance(begun, LoopRunResult):
                        return begun
                    runtime.current = begun
                    result = await self._execute_step(runtime, begun, opts)
                    completed = await self._complete_step(runtime, begun, result)
                    if completed is not None:
                        return completed
                except Exception as error:  # noqa: BLE001
                    disposition = await self._handle_step_error(runtime, error)
                    if disposition is None:
                        continue
                    return disposition
        finally:
            self._queue.abort_turn_scoped()

    # ----------------------------------------------------------------- step 生命周期

    async def _begin_step(self, runtime: _LoopRuntime, opts: LoopRunOptions):
        if runtime.signal.is_set():
            raise asyncio.CancelledError("loop cancelled")
        if not self._queue.has_pending_requests():
            return LoopRunResult.completed(runtime.steps, runtime.last_stop_reason == FinishReason.TRUNCATED)

        max_steps = self.loop_control.max_steps_per_turn
        if max_steps and max_steps > 0 and runtime.steps >= max_steps:
            raise create_max_steps_exceeded_error(max_steps)

        batch = self._queue.take_next_batch()
        if batch is None:
            return LoopRunResult.completed(runtime.steps)

        self._materialize(batch.driver)
        for merged in batch.merged:
            self._materialize(merged)

        runtime.steps += 1
        first = runtime.steps == 1
        # before-step hook
        ctx = BeforeStepContext(
            turn_id=runtime.turn_id,
            step=runtime.steps,
            first_step_of_turn=first,
            signal=runtime.signal,
        )
        await self.hooks.on_will_begin_step.run(ctx)

        if opts.on_started:
            opts.on_started(runtime.steps)

        return _StepRuntime(step_number=runtime.steps, batch=batch, driver=batch.driver)

    def _materialize(self, request: StepRequest) -> None:
        if request.state != StepRequestState.PENDING:
            return
        request.on_will_materialize()
        for msg in request.resolve_context_messages():
            self._history.append(msg)
        request.mark_materialized()

    async def _execute_step(
        self, runtime: _LoopRuntime, step: _StepRuntime, opts: LoopRunOptions
    ) -> _StepExecutionResult:
        on_event = self._on_event

        def emit(ev: LoopStreamEvent) -> None:
            if on_event:
                on_event(ev)

        # 复用 create_stream_part_handler 的映射逻辑为回调构造事件
        part_handler = create_stream_part_handler(emit)

        callbacks = GenerateCallbacks(
            on_token=lambda t: part_handler(_Part(text=t)),
            on_reasoning=lambda t: part_handler(_Part(thinking=t)),
            on_tool_call=lambda msg: [
                part_handler(_Part(tool_call_id=tc.id, tool_name=tc.name)) for tc in msg.tool_calls
            ],
        )
        gen_opts = GenerateOptions()
        result = await generate(
            self.provider,
            self.system_prompt,
            self.tools,
            self._history,
            callbacks=callbacks,
            options=gen_opts,
        )

        assistant = result.message
        self._history.append(assistant)
        self._final_message = assistant

        finish = result.finish_reason
        stop_turn = False

        if assistant.tool_calls:
            tool_options = {
                "signal": runtime.signal,
                "turn_id": runtime.turn_id,
                "trace": None,
            }
            async for tres in self.tool_executor.execute(assistant.tool_calls, tool_options):
                self._history.append(
                    create_tool_message(tres.tool_call_id, tres.output, name=_name_of(tres))
                )
                if tres.stop_turn:
                    stop_turn = True
            finish = FinishReason.COMPLETED if stop_turn else FinishReason.TOOL_CALLS
        elif finish == FinishReason.TOOL_CALLS:
            # 安全归并：模型声称 tool_calls 但无实际调用
            finish = FinishReason.OTHER

        return _StepExecutionResult(stop_reason=finish, hook_stop_turn=stop_turn)

    async def _complete_step(
        self, runtime: _LoopRuntime, step: _StepRuntime, result: _StepExecutionResult
    ) -> Optional[LoopRunResult]:
        ctx = AfterStepContext(
            turn_id=runtime.turn_id,
            step=step.step_number,
            first_step_of_turn=step.step_number == 1,
            signal=runtime.signal,
            usage=Usage(),
            finish_reason=result.stop_reason,
            stop_turn=result.hook_stop_turn,
        )
        await self.hooks.on_did_finish_step.run(ctx)
        runtime.last_stop_reason = result.stop_reason

        if result.stop_reason == FinishReason.FILTERED:
            raise LoopError("loop.filtered", "Provider safety policy blocked the response.")

        if not ctx.stop_turn:
            return None
        return LoopRunResult.completed(
            runtime.steps, truncated=result.stop_reason == FinishReason.TRUNCATED
        )

    # ----------------------------------------------------------------- 错误恢复

    async def _handle_step_error(self, runtime: _LoopRuntime, error: Any):
        if isinstance(error, asyncio.CancelledError) or runtime.signal.is_set():
            return LoopRunResult.cancelled(runtime.steps, error)
        return await self._try_recover(runtime, error)

    async def _try_recover(self, runtime: _LoopRuntime, error: Any) -> Optional[LoopRunResult]:
        current = runtime.current
        context = LoopErrorContext(
            turn_id=runtime.turn_id,
            step=current.step_number if current else runtime.steps,
            step_id=None,
            signal=runtime.signal,
            error=error,
            current_step=current,
            failed_driver=current.driver if current else None,
            retry=self._retry,
        )
        for handler in self._error_handlers:
            if handler.match(context):
                try:
                    ok = await handler.handle(context)
                except Exception as handler_error:  # noqa: BLE001
                    return LoopRunResult.failed(runtime.steps, handler_error)
                if ok:
                    return None  # 已恢复，继续循环
                return LoopRunResult.failed(runtime.steps, error)
        return LoopRunResult.failed(runtime.steps, error)

    def _retry(self, request: StepRequest, options: Optional[dict] = None) -> None:
        at = (options or {}).get("at", "head")
        self._queue.enqueue(request, at)

    # ----------------------------------------------------------------- 取消 / 状态

    def status(self) -> dict:
        return {
            "state": "running" if self._queue.has_pending_requests() else "idle",
            "has_pending_requests": self._queue.has_pending_requests(),
        }


# ----------------------------------------------------------------- 内部辅助

class _Part:
    """轻量 StreamedMessagePart 替身，仅用于把 generate 回调转成事件。"""
    __slots__ = ("type", "text", "thinking", "id", "name", "arguments_delta")

    def __init__(self, text=None, thinking=None, tool_call_id=None, tool_name=None):
        self.type = "text_delta" if text is not None else (
            "thinking_delta" if thinking is not None else "tool_call_part"
        )
        # 文本与思维都走 .text，便于 create_stream_part_handler 统一处理
        self.text = text if text is not None else thinking
        self.thinking = thinking
        self.id = tool_call_id
        self.name = tool_name
        self.arguments_delta = None


def _name_of(tres: ToolExecutionResult) -> Optional[str]:
    return getattr(tres, "name", None)
