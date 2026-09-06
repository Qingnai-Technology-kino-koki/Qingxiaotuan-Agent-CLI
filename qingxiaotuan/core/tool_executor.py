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
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)


# ================================================================ MCP 安全守卫

def _is_mcp_dangerous(tool_name: str, fn_args: str) -> bool:
    """检查 MCP 工具参数是否含危险操作 (红线扫描层)。

    MCP server 不可信: 对参数的**所有**文本字段递归送 safety_engine 做红线检测,
    不依赖工具名是否含危险关键词 —— 与 ``SecurityGate.decide_mcp_tool`` 的覆盖
    完全一致, 保证"有无 gate 的兜底路径给出相同安全保证" (此前的关键词+白名单字段
    子集会让非关键词命名的恶意 MCP 工具漏过)。

    提示词注入是另一威胁类 (指令覆盖/角色劫持), 由
    ``tools/mcp/security.MCPSecurityGuard.scan_tool_params`` 承担, 与本函数互补。
    """
    if not tool_name.startswith('mcp__'):
        return False
    args = _parse_args(fn_args)
    try:
        from ..ext.safety_engine import is_redline as _engine_is_redline
        for text in _iter_text_values(args):
            if (text or "").strip() and _engine_is_redline(text):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ================================================================ 参数/文本工具

def _parse_args(fn_args: Any) -> Dict[str, Any]:
    """把工具参数统一成 dict (支持 JSON 字符串 / dict 直传)。"""
    if isinstance(fn_args, dict):
        return fn_args
    if isinstance(fn_args, str):
        s = fn_args.strip()
        if s[:1] in ("{", "["):
            try:
                import json as _json
                parsed = _json.loads(s)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _iter_text_values(obj: Any):
    """递归收集对象中的字符串值 (命令/脚本/代码等可执行文本字段)。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_text_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_text_values(v)


def _redline_in(text: str) -> bool:
    """文本是否命中红线 (懒导入, 避免顶层重依赖)。"""
    try:
        from ..ext.safety_engine import is_redline as _is_redline
        return bool(text) and _is_redline(text)
    except Exception:  # noqa: BLE001
        return False


# ================================================================ 常量

# 只读工具白名单: 仅用于非注册表工具 (如 MCP) 的并行判定回退。
# 注册表内工具一律以各自的 read_only 标志为准 (见 _is_readonly_tool)。
PARALLEL_SAFE_TOOLS: Set[str] = {
    "read_file", "search_files", "glob", "list_dir",
    "git_status", "web_fetch", "web_search",
    "memory_search", "skill_list", "pipeline_list_tools",
    "codebase_map", "find_symbol", "find_references",
}

# 单个工具执行超时 (秒); 0 表示不限超时
DEFAULT_TOOL_TIMEOUT: float = 120.0

# 并行执行最大线程数
MAX_PARALLEL_WORKERS: int = 4

# 工具级别速率限制: 每个工具名 → (最大调用次数, 时间窗口秒)
# 注意: 必须使用注册表中的真实工具名, 否则限制对实际工具不生效。
TOOL_RATE_LIMITS: Dict[str, tuple[int, float]] = {
    "write_file": (10, 60.0),      # 1 分钟内最多 10 次写文件
    "edit_file": (20, 60.0),       # 1 分钟内最多 20 次编辑
    "run_shell": (15, 60.0),       # 1 分钟内最多 15 次命令
    "web_fetch": (30, 60.0),       # 1 分钟内最多 30 次 fetch
    "web_search": (20, 60.0),      # 1 分钟内最多 20 次搜索
}


# ================================================================ 执行器


class _ToolRateLimiter:
    """工具级别速率限制器: 滑动窗口计数。"""

    def __init__(self, limits: Optional[Dict[str, tuple[int, float]]] = None) -> None:
        self._limits = dict(limits or TOOL_RATE_LIMITS)
        self._counts: Dict[str, List[float]] = {}  # tool_name → [timestamps]

    def check(self, tool_name: str) -> Optional[str]:
        """检查工具是否超过速率限制。返回 None=允许, 字符串=拒绝原因。"""
        if tool_name not in self._limits:
            return None
        max_calls, window = self._limits[tool_name]
        now = _time.monotonic()
        # 清理过期记录
        if tool_name in self._counts:
            self._counts[tool_name] = [
                t for t in self._counts[tool_name] if now - t < window
            ]
        else:
            self._counts[tool_name] = []
        # 检查限制
        if len(self._counts[tool_name]) >= max_calls:
            return f"工具 {tool_name} 速率限制: {window:.0f}s 内最多 {max_calls} 次调用"
        self._counts[tool_name].append(now)
        return None

    def reset(self, tool_name: Optional[str] = None) -> None:
        """重置速率限制计数。"""
        if tool_name:
            self._counts.pop(tool_name, None)
        else:
            self._counts.clear()


class ToolExecutor:
    """批量工具调用执行器: 只读并行 + 写串行 + 超时控制 + 速率限制 + diff hook。

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
        telemetry: Any = None,
        trace_id: str = "",
        on_write: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.registry = registry
        self.messages = messages  # Agent 的消息列表 (tool 结果写入此列表)
        self._session_append = session_append or (lambda *a, **kw: None)
        self._tool_content_fn = tool_content_fn or (lambda x: x)
        self.timeout = timeout
        self.max_workers = max_workers
        self._telemetry = telemetry
        self._trace_id = trace_id
        # 写工具 hook: on_write(file_path, diff_text) → TUI 可挂载用于显示 diff
        self._on_write = on_write
        # 工具速率限制器
        self._rate_limiter = _ToolRateLimiter()
        # 系统级沙箱统一入口 (懒构建; False=不可用)
        self._sandbox = None
        self._kernel_cache = None

    # -------------------------------------------------------- 批量执行

    def _is_readonly_tool(self, fn_name: str) -> bool:
        """判断工具是否只读、可并行执行。

        优先依据注册表中工具的 ``read_only`` 标志（权威来源），
        避免硬编码工具名列表与注册表漂移导致并行策略失效。
        MCP 等非注册表工具回退到静态白名单。
        """
        registry_get = getattr(self.registry, "get", None)
        if registry_get is not None:
            tool = registry_get(fn_name)
            if tool is not None:
                return bool(getattr(tool, "read_only", False))
        return fn_name in PARALLEL_SAFE_TOOLS

    # -------------------------------------------------------- 统一安全闸门

    def _security_gate_check(self, fn_name: str, fn_args: Any, ctx: Any):
        """统一安全闸门 (fail-closed) + 审计溯源。

        返回 ``(denied, message)``:
        - ``denied=True`` 时不应当 dispatch (message 为拦截说明);
        - ``denied=False`` 时 message 为空。

        设计: 所有产生副作用的工具调用 (shell / mcp / 文件写 / 其它内置工具)
        都经此闸门裁决; 危险操作默认拒绝, 审计溯源默认开启。无内核/无审计器时
        降级为基础红线扫描, 但绝不放开危险操作 (fail-closed)。
        """
        kernel = getattr(ctx, "kernel", None)
        auditor = None
        gate = None
        if kernel is not None:
            try:
                auditor = kernel.get("security_auditor")
            except Exception:  # noqa: BLE001
                auditor = None
            try:
                from ..ext.security_gate import SecurityGate
                gate = SecurityGate(
                    yolo=bool(getattr(ctx, "yolo", False)),
                    trust_level=getattr(ctx, "trust_level", None),
                    remote=bool(getattr(ctx, "remote", False)),
                    auditor=auditor,
                )
            except Exception:  # noqa: BLE001
                gate = None

        args = _parse_args(fn_args)
        denied = None               # (message, reasons)
        recorded_via_gate = False

        # 安全策略引擎: YAML 声明式规则, deny 优先于一切
        try:
            from ..core.security_policy import get_policy_engine
            policy_engine = get_policy_engine(
                home=getattr(ctx, "home", None),
            )
            policy_verdict = policy_engine.evaluate_tool(fn_name, args)
            if policy_verdict.action == "deny":
                denied = (
                    f"[安全策略拦截] {policy_verdict.reason}",
                    [f"policy:{policy_verdict.rule_id}"],
                )
        except Exception:  # noqa: BLE001
            pass

        if fn_name.startswith("mcp__"):
            # MCP 校验点分两种威胁类, 互补, 不是重复:
            #   A) 红线层 (命令级致命/危险操作)  —— SecurityGate.decide_mcp_tool;
            #      无 gate 时回退到 _is_mcp_dangerous, 两者覆盖一致 (全字段递归红线)。
            #   B) 注入层 (指令覆盖/角色劫持)     —— MCPSecurityGuard.scan_tool_params。
            # 1) 红线扫描 (统一闸门覆盖所有文本字段; 无 gate 用同覆盖兜底)
            if gate is not None:
                v = gate.decide("mcp_tool", fn_name, args=args)
                recorded_via_gate = True
                if v.blocks():
                    denied = (
                        f"[安全拦截] MCP 工具 {fn_name} 的参数含危险操作, 已阻止执行。",
                        list(v.reasons),
                    )
            if denied is None and _is_mcp_dangerous(fn_name, fn_args):
                denied = (
                    f"[安全拦截] MCP 工具 {fn_name} 的参数包含危险操作, 已阻止执行。请检查参数内容。",
                    ["mcp_redline"],
                )
            # 2) 提示词注入扫描 (MCP server 不可信, 必须做; 注入 ≠ 红线, 独立威胁类)
            if denied is None:
                try:
                    from ..tools.mcp.security import get_mcp_security_guard
                    guard = get_mcp_security_guard()
                    scan = guard.scan_tool_params(
                        fn_name, args if isinstance(args, dict) else {}
                    )
                    if not getattr(scan, "safe", True):
                        threats = ", ".join(
                            str(t.get("description", ""))
                            for t in (getattr(scan, "threats", []) or [])[:3]
                        )
                        denied = (
                            f"[安全拦截] MCP 工具 {fn_name} 参数注入检测: {threats}",
                            ["mcp_injection"],
                        )
                except Exception:  # noqa: BLE001
                    pass

        elif fn_name == "run_shell":
            cmd = args.get("command", "") if isinstance(args, dict) else str(fn_args)
            cmd = str(cmd)
            # run_shell 的完整闸门在 tools/shell.py (网络出口/白名单/信任级)。
            # 此处只做 fail-closed 兜底: 硬红线命中即拦截 (与 shell.py 一致),
            # confirm/allow 交给 shell.py, 避免重复确认。
            if gate is not None:
                v = gate.decide_shell(cmd)
                if v.blocks():
                    denied = (
                        f"[安全拦截] 命令命中致命红线, 禁止自动执行: {cmd}",
                        list(v.reasons),
                    )
            if denied is None:
                try:
                    from ..ext.safety_engine import is_hard_redline as _hard
                    if _hard(cmd):
                        denied = (
                            f"[安全拦截] 命中致命红线, 禁止自动执行: {cmd}",
                            ["hard_redline"],
                        )
                except Exception:  # noqa: BLE001
                    pass

        elif fn_name in ("write_file", "edit_file", "str_replace"):
            path = ""
            content = ""
            if isinstance(args, dict):
                path = str(args.get("path", args.get("file_path", "")) or "")
                content = str(
                    args.get("content")
                    or args.get("new_string")
                    or args.get("new")
                    or ""
                )
            # 路径安全校验: 防目录遍历 / 敏感路径泄露 / 工作区越界
            if path and denied is None:
                try:
                    from ..core.path_safety import validate_path as _validate_path
                    workspace = getattr(ctx, "workspace", "") or ""
                    pv = _validate_path(path, action="write", workspace=workspace)
                    if pv.denied:
                        denied = (
                            f"[安全拦截] 路径安全校验失败: {pv.reason}",
                            pv.violations,
                        )
                except Exception:  # noqa: BLE001
                    pass
            if gate is not None and content:
                v = gate.decide("file_write", content, path=path)
                recorded_via_gate = True
                if v.blocks():
                    denied = (
                        f"[安全拦截] 文件写入被安全闸门拒绝: "
                        f"{v.reasons[0] if v.reasons else '策略命中'}",
                        list(v.reasons),
                    )
            if denied is None and content and _redline_in(content):
                denied = (f"[安全拦截] 写入内容含致命操作, 已阻止写入。", ["redline"])

        else:
            # 其它内置工具: 递归扫描所有文本参数红线 (纵深防御)
            for text in _iter_text_values(args):
                if text and _redline_in(text):
                    denied = (
                        f"[安全拦截] 工具 {fn_name} 的文本参数含危险操作, 已阻止执行。",
                        ["redline"],
                    )
                    break

        # 系统级沙箱: 统一 4 层滤网 (L0 意图 / L1 信任 / L2 资源 / L3 强隔离) 堵侧门。
        # 该闸门只做 DENY 短路与 CONFIRM 裁定; 不重复既有关键词/红线扫描, 而是用
        # 工作区信任分级 + 计划模式 + 域名白名单 + 强隔离可用性做纵深防御。
        if denied is None:
            denied = self._sandbox_gate_check(fn_name, fn_args, args, ctx)

        # 审计溯源: 拒绝默认记录 (允许记录在 gate.decide 覆盖的路径上完成)
        if denied is not None and not recorded_via_gate and auditor is not None:
            try:
                auditor.record(
                    module="tool_executor",
                    action="deny",
                    severity="critical",
                    input_summary=f"{fn_name}: {str(fn_args)[:400]}",
                    reasons=list(denied[1]),
                    context={"tool": fn_name, "via_gate": gate is not None},
                )
            except Exception:  # noqa: BLE001
                pass

        if denied is not None:
            return True, denied[0]
        return False, ""

    # -------------------------------------------------------- 系统级沙箱 (4 层统一滤网)

    def _sandbox_manager(self) -> Any:
        """懒构建沙箱统一入口 (从 ctx.kernel 配置读取 sandbox 策略并挂审计)。"""
        if self._sandbox is None:
            kernel = getattr(self, "_kernel_cache", None)
            try:
                from ..sandbox.manager import SandboxManager
                if kernel is not None:
                    # 统一走 from_kernel: 同一策略解析 + 挂接审计器/事件总线
                    self._sandbox = SandboxManager.from_kernel(kernel)
                else:
                    self._sandbox = SandboxManager()
            except Exception:  # noqa: BLE001 - 沙箱子系统故障不影响既有闸门
                self._sandbox = False
        return self._sandbox or None

    def _sandbox_gate_check(self, fn_name: str, fn_args: Any, args: Dict[str, Any], ctx: Any):
        """用 4 层沙箱滤网做统一纵深判定, 返回 ``(denied, message)``。

        只短路 DENY; CONFIRM 交由既有确认通道 (run_shell 由 shell.py 确认,
        其余工具走 ctx.confirm, 无通道则 fail-closed 拦截)。run_shell 的隔离执行
        选路由 shell 集成层消费 verdict.isolate, 此处不做隔离。
        """
        kernel = getattr(ctx, "kernel", None)
        if kernel is not None:
            self._kernel_cache = kernel
        manager = self._sandbox_manager()
        if manager is None:
            return None
        try:
            if fn_name == "run_shell":
                cmd = str(args.get("command", str(fn_args)) if isinstance(args, dict) else fn_args)
                verdict = manager.assess_command(cmd, ctx)
                if verdict.blocks:
                    return (
                        f"[沙箱拦截] {verdict.describe()}",
                        ["sandbox:" + verdict.layer],
                    )
                # 通知 shell 集成层这次命令需要隔离执行 (recorded on ctx.route_sandbox)
                if verdict.isolate:
                    try:
                        setattr(ctx, "_sandbox_verdict", verdict)
                    except Exception:  # noqa: BLE001
                        pass
                return None
            # 非 shell 工具: 全工具统一过意图+信任滤网 (堵文件写/MCP 侧门)
            denied, msg = manager.assess_tool(fn_name, fn_args, ctx)
            if denied:
                return (f"[沙箱拦截] {msg}", ["sandbox"])
            return None
        except Exception:  # noqa: BLE001 - 沙箱评估异常不阻断, 由既有闸门兜底
            return None

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
                fn_name not in exclude
                and self._is_readonly_tool(fn_name)
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
            span = self._telemetry.start_span(f"tool.{fn_name}", trace_id=self._trace_id) if self._telemetry is not None else None
            st = _time.monotonic()
            denied, block_msg = self._security_gate_check(fn_name, fn_args, ctx)
            if denied:
                result_text = block_msg
                log.warning("统一安全闸门拦截 (parallel): %s", fn_name)
            else:
                try:
                    result_text = self.registry.dispatch(fn_name, fn_args, ctx)
                except Exception as exc:  # noqa: BLE001
                    result_text = f"[错误] {fn_name}: {exc}"
            elapsed = (_time.monotonic() - st) * 1000
            if span is not None:
                self._telemetry.finish_span(span.span_id, status="ok")
            return (tc.get("id", ""), fn_name, result_text, elapsed)

        workers = min(len(tool_calls), self.max_workers)
        results: Dict[str, tuple] = {}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one, tc): tc for tc in tool_calls}
            for future in as_completed(futures):
                try:
                    if self.timeout > 0:
                        tc_id, fn_name, text, elapsed = future.result(timeout=self.timeout)
                    else:
                        tc_id, fn_name, text, elapsed = future.result()
                    results[tc_id] = (tc_id, fn_name, text, elapsed)
                except FutureTimeout:
                    tc = futures[future]
                    fn_name = tc["function"]["name"]
                    tc_id = tc.get("id", "")
                    results[tc_id] = (
                        tc_id, fn_name,
                        f"[超时] 工具 {fn_name} 在 {self.timeout}s 内未完成",
                        0.0,
                    )
                except Exception as exc:  # noqa: BLE001
                    tc = futures[future]
                    fn_name = tc["function"]["name"]
                    tc_id = tc.get("id", "")
                    results[tc_id] = (tc_id, fn_name, f"[错误] {fn_name}: {exc}", 0.0)

        # 按原始顺序写入消息 (保证 tool_call_id 与 assistant 消息的 tool_calls 对应)
        for tc in tool_calls:
            tc_id = tc.get("id", "")
            if tc_id in results:
                _, fn_name, result_text, elapsed = results[tc_id]
            else:
                fn_name = tc["function"]["name"]
                result_text = f"[错误] 工具 {fn_name} 结果丢失"
                elapsed = 0.0
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
            # 可观测性: 记录工具调用指标 (含最终 status)
            if self._telemetry is not None:
                status = tool_msg.get("status", _status_of(result_text))
                self._telemetry.record_tool_call(fn_name, elapsed, success=(status == "ok"))

    def _execute_single(
        self,
        tc: Dict[str, Any],
        ctx: Any,
        exclude: Set[str],
        on_tool: Optional[Callable] = None,
        on_tool_result: Optional[Callable] = None,
    ) -> None:
        """串行执行单个工具 (写工具/危险工具)。"""
        fn_name = tc["function"]["name"]
        fn_args = tc["function"].get("arguments", "{}")
        if on_tool:
            on_tool(fn_name, fn_args)
        self._session_append("tool_call", name=fn_name, arguments=fn_args)

        # 速率限制检查
        rate_msg = self._rate_limiter.check(fn_name)
        if rate_msg:
            result_text = f"[已限流] {rate_msg}"
            log.warning("工具速率限制: %s", fn_name)
            tool_msg = {
                "role": "tool", "tool_call_id": tc.get("id", ""),
                "name": fn_name, "content": result_text, "status": "denied",
            }
            if self.messages is not None:
                self.messages.append(tool_msg)
            if on_tool_result:
                on_tool_result(fn_name, result_text)
            return

        span = self._telemetry.start_span(f"tool.{fn_name}", trace_id=self._trace_id) if self._telemetry is not None else None
        _t0 = _time.monotonic()
        if fn_name in exclude:
            result_text = f"[已禁用] 工具 {fn_name} 已被 exclude_tools 排除"
        else:
            # 统一安全闸门 (fail-closed) + 审计溯源: 每个工具调用都过闸,
            # 危险操作默认拦截; 拒绝记录写入安全审计日志。
            denied, block_msg = self._security_gate_check(fn_name, fn_args, ctx)
            if denied:
                result_text = block_msg
                log.warning("统一安全闸门拦截: %s", fn_name)
                tool_msg = {
                    "role": "tool", "tool_call_id": tc.get("id", ""),
                    "name": fn_name, "content": result_text, "status": "denied",
                }
                if self.messages is not None:
                    self.messages.append(tool_msg)
                if on_tool_result:
                    on_tool_result(fn_name, result_text)
                return
            result_text = self.registry.dispatch(fn_name, fn_args, ctx)
        _elapsed = _time.monotonic() - _t0
        if _elapsed > 0.5:
            log.debug("tool %s completed in %.2fs", fn_name, _elapsed)
        if span is not None:
            self._telemetry.finish_span(span.span_id, status="ok")

        # Diff hook: 写工具执行成功后生成 diff 供 TUI 展示
        if self._on_write and fn_name in ("write_file", "edit_file", "str_replace"):
            try:
                import json as _json
                args_dict = _json.loads(fn_args) if isinstance(fn_args, str) else fn_args
                if isinstance(args_dict, dict):
                    file_path = args_dict.get("path", args_dict.get("file_path", ""))
                    if file_path and str(result_text).startswith(("已写入", "已修改", "编辑成功")):
                        # 生成简化 diff
                        diff = self._make_diff_summary(fn_name, args_dict, str(result_text))
                        if diff:
                            self._on_write(file_path, diff)
            except Exception:  # noqa: BLE001
                pass  # diff 生成失败不阻断主流程

        # 自动检查点: 写工具成功后, 若注册了 checkpoint_store 服务则自动落一个检查点
        if fn_name in ("write_file", "edit_file", "str_replace") and _is_ok(result_text):
            try:
                cp = self._auto_checkpoint(fn_name, fn_args, ctx)
            except Exception:  # noqa: BLE001
                log.warning("自动检查点失败: %s", fn_name)
        # ----

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
        # 可观测性: 记录工具调用指标
        if self._telemetry is not None:
            status = tool_msg.get("status", _status_of(result_text))
            self._telemetry.record_tool_call(fn_name, _elapsed * 1000, success=(status == "ok"))

    def _auto_checkpoint(self, fn_name: str, fn_args: Any, ctx: Any) -> Any:
        """写工具成功后自动建检查点 (从 ctx.kernel 取 checkpoint_store 服务)。"""
        kernel = getattr(ctx, "kernel", None)
        if kernel is None:
            return None
        store = kernel.get("checkpoint_store")
        if store is None:
            try:
                store = getattr(ctx, "checkpoint_store", None)
            except Exception:  # noqa: BLE001
                store = None
        if store is None:
            return None
        files: List[str] = []
        try:
            import json as _json
            args_dict = _json.loads(fn_args) if isinstance(fn_args, str) else fn_args
            if isinstance(args_dict, dict):
                path = args_dict.get("path", args_dict.get("file_path", ""))
                if path:
                    files.append(str(path))
        except Exception:  # noqa: BLE001
            pass
        auto = getattr(store, "auto", True)
        if not auto:
            return None
        try:
            return store.auto_checkpoint(files, summary=f"自动: {fn_name} 修改 {', '.join(files) or '(未知文件)'}")
        except Exception:  # noqa: BLE001
            return None


def _make_diff_summary(fn_name: str, args: Dict[str, Any], result: str) -> Optional[str]:
    """为写工具生成简化的 diff 摘要, 供 TUI 内联展示。"""
    try:
        if fn_name == "write_file":
            content = args.get("content", "")
            lines = content.split("\n")
            added = len([l for l in lines if l.strip()])
            return f"+ {added} 行写入"
        elif fn_name in ("edit_file", "str_replace"):
            old = args.get("old_string", args.get("old", ""))
            new = args.get("new_string", args.get("new", ""))
            old_lines = old.split("\n") if old else []
            new_lines = new.split("\n") if new else []
            diff_lines = []
            for line in old_lines:
                diff_lines.append(f"- {line}")
            for line in new_lines:
                diff_lines.append(f"+ {line}")
            return "\n".join(diff_lines[:30])  # 最多 30 行
    except Exception:  # noqa: BLE001
        pass
    return None


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


def _is_ok(result_text: Any) -> bool:
    return _status_of(result_text) in ("ok", "cached")
