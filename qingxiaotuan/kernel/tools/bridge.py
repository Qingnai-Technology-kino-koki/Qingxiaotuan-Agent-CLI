"""bridge.py —— 把现有 Python 青小团工具系统接入 kernel 子树。

1. ``wrap_existing_tool``：把 ``qingxiaotuan.tools.base.Tool`` 适配成 kernel 的
   ``ExecutableTool``（handler 调用 + 按 dangerous/read_only 推导 approval_rule 与 accesses）。
2. ``KernelToolExecutorAdapter``：把现有 ``core/tool_executor.ToolExecutor`` 包装成
   kernel 风格的异步生成器 ``execute``，让既有引擎（只读并行 + 写串行）在 kernel 层可用。

为避免在 ``import qingxiaotuan.kernel.tools`` 时连带拉起全部内置工具插件，
对 ``qingxiaotuan.tools.base`` 与 ``core.tool_executor`` 采用惰性导入。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, List, Optional

from .contract import (
    ExecutableToolContext,
    ExecutableToolResult,
    RunnableToolExecution,
    ToolAccesses,
)
from .executor import ToolExecutionResult


def wrap_existing_tool(
    tool: Any,
    *,
    workspace: str = ".",
    source: str = "builtin",
) -> "_WrappedTool":
    """把 ``qingxiaotuan.tools.base.Tool`` 适配成 kernel ``ExecutableTool``。

    推导规则：
    - read_only=True  -> approval_rule="read",  accesses=none()（可并行）
    - dangerous=True  -> approval_rule="dangerous", accesses=all()（保守串行）
    - 其它            -> approval_rule="default",  accesses=all()
    """
    return _WrappedTool(tool, workspace=workspace, source=source)


class _WrappedTool:
    """``tools.base.Tool`` 的 kernel``ExecutableTool`` 包装。"""

    def __init__(self, tool: Any, *, workspace: str, source: str) -> None:
        self._tool = tool
        self._workspace = workspace
        self._source = source

    # ---- ExecutableTool 接口字段 ----
    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return self._tool.description

    @property
    def parameters(self) -> Any:
        return self._tool.parameters

    @property
    def source(self) -> str:
        return self._source

    # ---- 资源访问推导 ----
    def _approval_rule(self) -> str:
        if getattr(self._tool, "read_only", False):
            return "read"
        if getattr(self._tool, "dangerous", False):
            return "dangerous"
        return "default"

    def _accesses(self) -> ToolAccesses:
        if getattr(self._tool, "read_only", False):
            return ToolAccesses.none()
        return ToolAccesses.all()

    # ---- resolve_execution ----
    def resolve_execution(self, input: Any) -> RunnableToolExecution:
        args = input if isinstance(input, dict) else {}

        async def execute(ctx: ExecutableToolContext) -> ExecutableToolResult:
            return await _run_base_handler(self._tool, args, self._workspace)

        return RunnableToolExecution(
            approval_rule=self._approval_rule(),
            execute=execute,
            accesses=self._accesses(),
        )


async def _run_base_handler(tool: Any, args: dict, workspace: str) -> ExecutableToolResult:
    # 惰性导入：避免 import 期拉起全部工具插件
    from qingxiaotuan.tools.base import ToolContext

    base_ctx = ToolContext(kernel=None, workspace=workspace)
    try:
        out = tool.handler(base_ctx, **args)
        if asyncio.iscoroutine(out):
            out = await out
    except Exception as exc:  # noqa: BLE001
        return ExecutableToolResult(
            output=f"Tool {tool.name} failed: {type(exc).__name__}: {exc}", is_error=True
        )
    return ExecutableToolResult(output=str(out), is_error=False)


# ----------------------------------------------------- 适配现有 core 执行器


class KernelToolExecutorAdapter:
    """把 ``core/tool_executor.ToolExecutor`` 包装成 kernel 风格的异步生成器。

    既有的「只读并行 + 写串行 + 超时」批处理策略原样复用，仅做调用契约转换：
    kernel ``ToolCall`` -> 既有 dict 形式 -> core.execute_batch -> 结果回读为
    ``ToolExecutionResult``。
    """

    def __init__(self, core_executor: Any, registry: Any = None) -> None:
        # 惰性导入，仅在使用时校验类型
        from qingxiaotuan.core.tool_executor import ToolExecutor as CoreToolExecutor

        if not isinstance(core_executor, CoreToolExecutor):
            raise TypeError("core_executor 必须是 qingxiaotuan.core.tool_executor.ToolExecutor")
        self._core = core_executor
        self._registry = registry

    async def execute(
        self,
        calls: List[Any],
        *,
        signal: Any = None,
        turn_id: int = 0,
        trace: Any = None,
        on_tool_call: Optional[Callable[[dict], None]] = None,
    ) -> AsyncIterator[ToolExecutionResult]:
        from qingxiaotuan.tools.base import ToolContext

        messages: List[dict] = []
        core_calls = [
            {"id": c.id, "function": {"name": c.name, "arguments": c.arguments}} for c in calls
        ]
        ctx = ToolContext(kernel=None, workspace=".")

        def _on_tool(name: str, args: str) -> None:
            if on_tool_call is not None:
                on_tool_call({"tool_call_id": "", "name": name, "args": args})

        saved = self._core.messages
        self._core.messages = messages
        try:
            await asyncio.to_thread(
                self._core.execute_batch, core_calls, ctx, None, _on_tool, None
            )
        finally:
            self._core.messages = saved

        for m in messages:
            if m.get("role") != "tool":
                continue
            yield ToolExecutionResult(
                tool_call_id=m.get("tool_call_id", ""),
                tool_name=m.get("name", ""),
                result=ExecutableToolResult(
                    output=m.get("content", ""),
                    is_error=(m.get("status") != "ok"),
                ),
            )


__all__ = ["wrap_existing_tool", "KernelToolExecutorAdapter"]
