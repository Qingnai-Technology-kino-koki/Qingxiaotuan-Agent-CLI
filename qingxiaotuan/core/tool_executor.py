"""工具执行引擎 —— 从 Agent 拆出的独立组件。

职责:
- 批量工具调用的分批策略 (只读并行 + 写串行)
- 单工具串行执行 + 超时控制
- 只读工具并发执行 (ThreadPoolExecutor)
- 工具结果 → 消息的转换

拆出原因:
- Agent 723 行中约 200 行是工具执行逻辑, 职责边界与 Agent 主循环混杂
- DevLoop、Swarm Worker、Nudge 等调用方也需要工具执行, 共享同一实现避免重复
- 并行策略 (只读白名单 + 写串行) 是独立的调度决策, 与 ReAct 循环解耦
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)


# ================================================================ 常量

# 只读工具白名单: 这些工具无副作用, 可安全并发执行
PARALLEL_SAFE_TOOLS: Set[str] = {
    "read_file", "read_files", "code_search", "glob", "list_directory",
    "git_status", "git_log", "git_diff", "web_fetch", "web_search",
    "memory_search", "skill_list", "pipeline_list_tools",
}

# 单个工具执行超时 (秒); 0 表示不限超时
DEFAULT_TOOL_TIMEOUT: float = 120.0

# 并行执行最大线程数
MAX_PARALLEL_WORKERS: int = 4


# ================================================================ 执行器


class ToolExecutor:
    """批量工具调用执行器: 只读并行 + 写串行 + 超时控制。

    用法::

        executor = ToolExecutor(
            registry=kernel.require("tool_registry"),
            session_append=agent._session_append,
            tool_content_fn=agent._tool_content,
        )
        executor.execute_batch(tool_calls, exclude_tools, ctx, ...)
    """

    def __init__(
        self,
        registry: Any,
        messages: Optional[List[Dict[str, Any]]] = None,
        session_append: Optional[Callable[..., None]] = None,
        tool_content_fn: Optional[Callable[[Any], Any]] = None,
        timeout: float = DEFAULT_TOOL_TIMEOUT,
        max_workers: int = MAX_PARALLEL_WORKERS,
    ) -> None:
        self.registry = registry
        self.messages = messages  # Agent 的消息列表 (tool 结果写入此列表)
        self._session_append = session_append or (lambda *a, **kw: None)
        self._tool_content_fn = tool_content_fn or (lambda x: x)
        self.timeout = timeout
        self.max_workers = max_workers

    # -------------------------------------------------------- 批量执行

    def execute_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        ctx: Any,
        exclude_tools: Optional[Set[str]] = None,
        on_tool: Optional[Callable[[str, str], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """执行一批工具调用。

        并行策略:
        - 连续的只读工具 → ThreadPoolExecutor 并发 (减小延迟)
        - 写工具 / 危险工具 → 严格串行 (保证顺序与副作用可控)
        - 只读块和写块交替时, 先等并行块完成再串行执行写块
        """
        exclude = exclude_tools or set()
        batches: list[tuple[bool, list]] = []  # (parallel, calls)
        current_parallel: list = []

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            is_readonly = (
                fn_name in PARALLEL_SAFE_TOOLS
                and fn_name not in exclude
            )
            if is_readonly:
                current_parallel.append(tc)
            else:
                if current_parallel:
                    batches.append((True, current_parallel))
                    current_parallel = []
                batches.append((False, [tc]))
        if current_parallel:
            batches.append((True, current_parallel))

        for parallel, calls in batches:
            if parallel and len(calls) > 1:
                self._execute_parallel(calls, ctx, exclude, on_tool, on_tool_result)
            else:
                for tc in calls:
                    self._execute_single(tc, ctx, exclude, on_tool, on_tool_result)

    # -------------------------------------------------------- 并行执行

    def _execute_parallel(
        self,
        tool_calls: List[Dict[str, Any]],
        ctx: Any,
        exclude: Set[str],
        on_tool: Optional[Callable] = None,
        on_tool_result: Optional[Callable] = None,
    ) -> None:
        """并发执行多个只读工具。"""

        def _run_one(tc: Dict) -> tuple:
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", "{}")
            if on_tool:
                on_tool(fn_name, fn_args)
            self._session_append("tool_call", name=fn_name, arguments=fn_args)
            result_text = self.registry.dispatch(fn_name, fn_args, ctx)
            return (tc.get("id", ""), fn_name, result_text)

        workers = min(len(tool_calls), self.max_workers)
        results: Dict[str, tuple] = {}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one, tc): tc for tc in tool_calls}
            for future in as_completed(futures):
                try:
                    if self.timeout > 0:
                        tc_id, fn_name, text = future.result(timeout=self.timeout)
                    else:
                        tc_id, fn_name, text = future.result()
                    results[tc_id] = (tc_id, fn_name, text)
                except FutureTimeout:
                    tc = futures[future]
                    fn_name = tc["function"]["name"]
                    tc_id = tc.get("id", "")
                    results[tc_id] = (
                        tc_id, fn_name,
                        f"[超时] 工具 {fn_name} 在 {self.timeout}s 内未完成",
                    )
                except Exception as exc:  # noqa: BLE001
                    tc = futures[future]
                    fn_name = tc["function"]["name"]
                    tc_id = tc.get("id", "")
                    results[tc_id] = (tc_id, fn_name, f"[错误] {fn_name}: {exc}")

        # 按原始顺序写入消息 (保证 tool_call_id 与 assistant 消息的 tool_calls 对应)
        for tc in tool_calls:
            tc_id = tc.get("id", "")
            if tc_id in results:
                _, fn_name, result_text = results[tc_id]
            else:
                fn_name = tc["function"]["name"]
                result_text = f"[错误] 工具 {fn_name} 结果丢失"
            if on_tool_result:
                on_tool_result(fn_name, str(result_text))
            tool_msg = {
                "role": "tool",
                "tool_call_id": tc_id,
                "name": fn_name,
                "content": self._tool_content_fn(result_text),
                "status": _status_of(result_text),
            }
            if self.messages is not None:
                self.messages.append(tool_msg)
            self._session_append("tool", message=tool_msg, name=fn_name, content=str(result_text))

    def _execute_single(
        self,
        tc: Dict[str, Any],
        ctx: Any,
        exclude: Set[str],
        on_tool: Optional[Callable] = None,
        on_tool_result: Optional[Callable] = None,
    ) -> None:
        """串行执行单个工具 (写工具/危险工具)。"""
        import time as _time
        fn_name = tc["function"]["name"]
        fn_args = tc["function"].get("arguments", "{}")
        if on_tool:
            on_tool(fn_name, fn_args)
        self._session_append("tool_call", name=fn_name, arguments=fn_args)

        _t0 = _time.monotonic()
        if fn_name in exclude:
            result_text = f"[已禁用] 工具 {fn_name} 已被 exclude_tools 排除"
        else:
            result_text = self.registry.dispatch(fn_name, fn_args, ctx)
        _elapsed = _time.monotonic() - _t0
        # verbose: 输出工具执行耗时
        if _elapsed > 0.5:
            log.debug("tool %s completed in %.2fs", fn_name, _elapsed)

        if on_tool_result:
            on_tool_result(fn_name, str(result_text))

        tool_msg = {
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "name": fn_name,
            "content": self._tool_content_fn(result_text),
            "status": _status_of(result_text),
        }
        if self.messages is not None:
            self.messages.append(tool_msg)
        self._session_append("tool", message=tool_msg, name=fn_name, content=str(result_text))


def _status_of(result_text: Any) -> str:
    """从工具执行结果推断 status 字符串 (ok|error|denied|timeout|cached)。

    优先用 ToolResult.status; 若结果是文本 (异常分支/包装), 用项目约定的错误前缀
    '[错误]/[拒绝]/[超时]' 推断。该 status 写入 tool 消息, 供 agent 程序化判断成败
    (如自动路由的卡住升级), 不改动原来的 content 文本。
    """
    status = getattr(result_text, "status", None)
    if isinstance(status, str) and status:
        return status
    text = str(result_text)
    for marker, st in (("[错误]", "error"), ("[拒绝", "denied"), ("[超时]", "timeout"),
                        ("[已禁用]", "denied"), ("[denied]", "denied")):
        if text.startswith(marker):
            return st
    return "ok"
