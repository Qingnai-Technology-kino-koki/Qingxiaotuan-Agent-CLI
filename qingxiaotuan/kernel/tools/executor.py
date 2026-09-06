"""executor —— 工具执行器：调用可执行工具并收拢结果。

``ToolExecutor.execute`` 流程（简化 port，保留核心）：
1. preflight：解析参数 -> 注册表 resolve -> schema 校验；失败合成 error 结果。
2. prepare ：resolve_execution -> 触发 before_execute 钩子（权限闸门在此订阅）
            -> 封装成 SchedulerTask（含 accesses 与 execute）。
3. scheduler：按 ToolAccesses.conflict() 细粒度调度（读并行 / 写串行）。
4. yield   ：按调用顺序逐个产出归一化后的 ToolExecutionResult。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    List,
    Optional,
)

from ..contract import ToolCall
from .args import parse_tool_call_arguments, validate_tool_args
from .before_execute_event import BeforeExecuteDecision, BeforeToolExecuteEvent
from .contract import (
    ExecutableTool,
    ExecutableToolContext,
    ExecutableToolResult,
    RunnableToolExecution,
    ToolAccesses,
)
from .registry import ToolRegistry
from .scheduler import SchedulerTask, ToolScheduler

TOOL_OUTPUT_EMPTY = "Tool output is empty."
TOOL_OUTPUT_NON_TEXT = "Tool returned non-text content."


# ---------------------------------------------------------------- 结果结构


@dataclass
class ToolExecutionResult:
    tool_call_id: str
    tool_name: str
    result: ExecutableToolResult


@dataclass
class PreparedTask:
    task: SchedulerTask[ExecutableToolResult]
    stop_batch_after_this: bool = False


class _Disposable:
    def __init__(self, unsubscribe: Callable[[], None]) -> None:
        self._unsubscribe = unsubscribe

    def dispose(self) -> None:
        self._unsubscribe()


# ---------------------------------------------------------------- 工具函数


def make_error_result(output: str, stop_turn: bool = False) -> ExecutableToolResult:
    return ExecutableToolResult(output=output, is_error=True, stop_turn=stop_turn)


def _coerce(result: Any, tool_name: str) -> ExecutableToolResult:
    if result is None:
        return make_error_result(f'Tool "{tool_name}" returned no result.')
    if not isinstance(result, ExecutableToolResult):
        if isinstance(result, dict) and "output" in result:
            try:
                return ExecutableToolResult(**result)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                pass
        return make_error_result(
            f'Tool "{tool_name}" returned a malformed result (missing "output").'
        )
    return result


def _normalize(result: ExecutableToolResult) -> ExecutableToolResult:
    out = result.output
    if isinstance(out, str):
        output: Any = out if out else TOOL_OUTPUT_EMPTY
    elif isinstance(out, list):
        if not out:
            output = TOOL_OUTPUT_EMPTY
        else:
            text = "".join(
                getattr(p, "text", "") or "" for p in out if getattr(p, "type", None) == "text"
            )
            output = text if text else TOOL_OUTPUT_NON_TEXT
    else:
        output = str(out)
    return ExecutableToolResult(
        output=output,
        is_error=result.is_error,
        stop_turn=result.stop_turn,
        truncated=result.truncated,
        note=result.note,
        spill=result.spill,
        description=result.description,
        approval_rule=result.approval_rule,
        stop_batch_after_this=result.stop_batch_after_this,
    )


def _is_aborted(signal: Any) -> bool:
    if signal is None:
        return False
    return bool(getattr(signal, "aborted", False))


# ---------------------------------------------------------------- 执行器


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        subscribers: Optional[List[Callable[[BeforeToolExecuteEvent], Awaitable[None]]]] = None,
        trace: Any = None,
    ) -> None:
        self.registry = registry
        self.trace = trace
        self._before_execute: List[Callable[[BeforeToolExecuteEvent], Awaitable[None]]] = list(
            subscribers or []
        )
        self._scheduler = ToolScheduler[ExecutableToolResult]()
        self.guard: Optional[Callable[[str, str], Optional[str]]] = None

    # ---- 订阅 before_execute（权限闸门挂这里）-------------------------------

    def subscribe_before_execute(
        self, cb: Callable[[BeforeToolExecuteEvent], Awaitable[None]]
    ) -> _Disposable:
        self._before_execute.append(cb)

        def unsubscribe() -> None:
            if cb in self._before_execute:
                self._before_execute.remove(cb)

        return _Disposable(unsubscribe)

    # ---- 主入口 ------------------------------------------------------------

    async def execute(
        self,
        calls: List[ToolCall],
        *,
        signal: Any = None,
        turn_id: int = 0,
        trace: Any = None,
        on_tool_call: Optional[Callable[[dict], None]] = None,
    ) -> AsyncIterator[ToolExecutionResult]:
        if not calls:
            return

        preflighted = [self._preflight(c) for c in calls]
        prepared: List[PreparedTask] = []
        stop_batch = False

        for pre in preflighted:
            if on_tool_call is not None:
                on_tool_call(
                    {
                        "tool_call_id": pre.tool_call.id,
                        "name": pre.tool_name,
                        "args": pre.args if pre.kind == "runnable" else {},
                    }
                )

            if stop_batch:
                prepared.append(self._skipped_task(pre))
                continue

            p = await self._prepare(pre, calls, signal, turn_id, trace)
            prepared.append(p)
            if p.stop_batch_after_this:
                stop_batch = True

        futures = [self._scheduler.add(p.task) for p in prepared]
        results: List[ExecutableToolResult] = []
        for fut in futures:
            try:
                results.append(await fut)
            except Exception as exc:  # noqa: BLE001
                results.append(make_error_result(f"Tool failed: {exc}"))

        for pre, result in zip(preflighted, results):
            yield ToolExecutionResult(
                tool_call_id=pre.tool_call.id,
                tool_name=pre.tool_name,
                result=_normalize(result),
            )

    # ---- preflight ---------------------------------------------------------

    def _preflight(self, call: ToolCall) -> "_Preflighted":
        parsed = parse_tool_call_arguments(call.arguments)
        args = parsed.data
        tool = self.registry.resolve(call.name)
        if tool is None:
            return _Preflighted(
                kind="rejected",
                tool_call=call,
                tool_name=call.name,
                args=args,
                output=f'Tool "{call.name}" not found',
            )
        if self.guard is not None:
            denied = self.guard(call.name, "builtin")
            if denied is not None:
                return _Preflighted(
                    kind="rejected", tool_call=call, tool_name=call.name, args=args, output=denied
                )
        validation_error = validate_tool_args(getattr(tool, "parameters", None), args)
        if validation_error is not None:
            return _Preflighted(
                kind="rejected",
                tool_call=call,
                tool_name=call.name,
                args=args,
                output=f'Invalid args for tool "{call.name}": {validation_error}',
            )
        return _Preflighted(
            kind="runnable", tool_call=call, tool_name=call.name, tool=tool, args=args
        )

    # ---- prepare -----------------------------------------------------------

    async def _prepare(
        self,
        pre: "_Preflighted",
        all_calls: List[ToolCall],
        signal: Any,
        turn_id: int,
        trace: Any,
    ) -> PreparedTask:
        if pre.kind == "rejected":
            return PreparedTask(
                task=SchedulerTask(accesses=ToolAccesses.none(), run=_const(make_error_result(pre.output)))
            )

        tool = pre.tool
        assert tool is not None
        try:
            execution = tool.resolve_execution(pre.args)
            if hasattr(execution, "__await__"):
                execution = await execution
        except Exception as exc:  # noqa: BLE001
            return PreparedTask(
                task=SchedulerTask(
                    accesses=ToolAccesses.none(),
                    run=_const(
                        make_error_result(
                            f'Tool "{pre.tool_name}" failed to resolve execution: {exc}'
                        )
                    ),
                )
            )

        if isinstance(execution, ExecutableToolResult) and execution.is_error:
            return PreparedTask(
                task=SchedulerTask(accesses=ToolAccesses.none(), run=_const(execution))
            )

        execution = _as_runnable(execution)

        if _is_aborted(signal):
            return PreparedTask(
                task=SchedulerTask(
                    accesses=ToolAccesses.none(),
                    run=_const(make_error_result(f'Tool "{pre.tool_name}" was aborted')),
                )
            )

        event = BeforeToolExecuteEvent(
            turn_id=turn_id,
            signal=signal,
            trace=trace if trace is not None else self.trace,
            tool_call=pre.tool_call,
            tool_calls=all_calls,
            tool=tool,
            args=pre.args,
            execution=execution,
        )
        decision = await event.fire(self._before_execute)

        if decision is not None and decision.veto is not None:
            return PreparedTask(
                task=SchedulerTask(accesses=ToolAccesses.none(), run=_const(decision.veto))
            )

        async def _run() -> ExecutableToolResult:
            ctx = ExecutableToolContext(
                turn_id=turn_id,
                tool_call_id=pre.tool_call.id,
                signal=signal,
                trace=event.trace,
                metadata=decision.execution_metadata if decision else None,
            )
            try:
                raw = await execution.execute(ctx)
            except Exception as exc:  # noqa: BLE001
                return make_error_result(f'Tool "{pre.tool_name}" failed: {exc}')
            return _coerce(raw, pre.tool_name)

        return PreparedTask(
            task=SchedulerTask(accesses=execution.accesses, run=_run),
            stop_batch_after_this=bool(getattr(execution, "stop_batch_after_this", False)),
        )

    def _skipped_task(self, pre: "_Preflighted") -> PreparedTask:
        return PreparedTask(
            task=SchedulerTask(
                accesses=ToolAccesses.none(),
                run=_const(
                    make_error_result(
                        "Tool skipped because a previous tool call stopped the turn."
                    )
                ),
            )
        )


# ---------------------------------------------------------------- 内部结构 / 小工具


@dataclass
class _Preflighted:
    kind: str  # 'runnable' | 'rejected'
    tool_call: ToolCall
    tool_name: str
    tool: Optional[ExecutableTool] = None
    args: Any = None
    output: str = ""  # rejected 时的错误信息


def _const(value: ExecutableToolResult) -> Callable[[], Awaitable[ExecutableToolResult]]:
    async def _always() -> ExecutableToolResult:
        return value

    return _always


def _as_runnable(execution: Any) -> RunnableToolExecution:
    """把 ToolExecution 联合类型统一成 RunnableToolExecution。"""
    if isinstance(execution, RunnableToolExecution):
        return execution
    return RunnableToolExecution(
        approval_rule="",
        execute=_const(execution),
        accesses=ToolAccesses.none(),
    )


__all__ = ["ToolExecutor", "ToolExecutionResult", "BeforeExecuteDecision"]
