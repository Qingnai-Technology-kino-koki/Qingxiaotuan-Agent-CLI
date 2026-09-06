"""执行层 — 5 种事件语义 + 工具管线 waterfall + 可插拔 Agent Loop 桥接。

核心抽象:

1. 5 种事件语义 (EventSemantics) —— 所有层统一使用的"语言":
     EMIT     过程向外部发出动作 (tool call / 消息)
     OBSERVE  环境返回的观察 (工具结果 / 模型响应)
     DECIDE   智能体做出决策 (下一步动作 / 路由)
     REFLECT  自我反思 / 批判 (对上一个动作的评估)
     TERMINATE 回合结束 / 把控制权交还编排层
   通过一个 SemanticBus 发布/订阅, 各层 (安全/编排/可观测) 都能对同一语义做响应。

2. 工具管线 waterfall —— ToolPipeline 把一次工具调用拆成 pre -> gate -> execute
   -> post -> audit 五个阶段, 可注入自定义 stage, 并天然接入既有 HookManager
   (pre/post execute) 与审计 (emit kernel event tool.executed)。
   gate 拦截也通过 DECIDE 语义记录到 SemanticBus, 保证安全决策可审计。

3. LoopProviderBridge —— 把 arch 层的 BaseAgentLoop 桥接为 core.loop_provider.LoopProvider,
   使 arch 层的轻量可测试 Loop 能直接用于 Agent 主循环。桥接器通过
   agent._execute_tools() 执行工具, 保留完整安全管线 (ToolPipeline/HookManager/
   事务账本/MCP 安全加固)。

注: LoopRegistry / BaseAgentLoop / ReActLoop 已迁移到 core.loop_provider,
本模块不再重复定义, 仅导出 core.loop_provider 的对应类以保持 API 兼容。
"""

from __future__ import annotations

import time
import uuid
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


# ============================================================ 5 种事件语义
class EventSemantics(str, Enum):
    """执行层统一的事件语义。所有层都围绕这 5 种语义协作。"""

    EMIT = "emit"                       # 过程 -> 外部: 工具调用 / 消息
    OBSERVE = "observe"                 # 外部 -> 过程: 工具结果 / 模型响应
    DECIDE = "decide"                   # 智能体决策: 下一步动作 / 路由
    REFLECT = "reflect"                 # 自我反思: 对上一步的评估
    TERMINATE = "terminate"             # 回合结束 / 交还编排层

    def __str__(self) -> str:  # 便于直接当字符串用
        return self.value


@dataclass
class SemanticEvent:
    """携带一次语义事件的不可变记录。"""

    kind: EventSemantics
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: float = field(default_factory=time.time)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": str(self.kind),
            "source": self.source,
            "payload": self.payload,
            "seq": self.seq,
            "ts": self.ts,
            "correlation_id": self.correlation_id,
        }


class SemanticBus:
    """5 种语义事件的发布/订阅总线。

    - subscribe(kind, handler): 订阅某语义 (或 "*" 收全部)
    - publish(event): 广播事件, 经中间件链后分发给订阅者
    - 中间件可在事件传播前后改写/拦截 (用于安全层阻断、可观测层记录)
    - handler 异常隔离: 一个 handler 失败不影响其余; 错误被收集到 error_log
    - stats() 返回发布/错误统计, 便于监控
    """

    # 错误积累上限: 避免长时间运行时内存无限增长
    _MAX_ERRORS: int = 200
    # 历史记录上限: 超出后丢弃最旧的
    _MAX_HISTORY: int = 1000

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[[SemanticEvent], None]]] = {}
        self._middlewares: List[Callable[[SemanticEvent], SemanticEvent]] = []
        self._seq = 0
        self._history: List[SemanticEvent] = []
        # ---- 统计 ----
        self._published_count: int = 0
        self._handler_errors: List[Dict[str, Any]] = []  # [{event_kind, handler, error, ts}]
        self._middleware_errors: List[Dict[str, Any]] = []
        self._handler_call_counts: Dict[str, int] = {}   # handler_name -> 调用次数
        self._blocked_count: int = 0                     # 被中间件拦截 (返回 None) 的事件数

    def subscribe(self, kind: "EventSemantics | str", handler: Callable[[SemanticEvent], None]) -> None:
        key = kind.value if isinstance(kind, EventSemantics) else str(kind)
        self._handlers.setdefault(key, []).append(handler)

    def unsubscribe(self, kind: "EventSemantics | str", handler: Callable[[SemanticEvent], None]) -> bool:
        """移除指定 handler。返回是否成功移除。"""
        key = kind.value if isinstance(kind, EventSemantics) else str(kind)
        handlers = self._handlers.get(key, [])
        try:
            handlers.remove(handler)
            return True
        except ValueError:
            return False

    def add_middleware(self, mw: Callable[[SemanticEvent], SemanticEvent]) -> None:
        self._middlewares.append(mw)

    def publish(self, event: SemanticEvent) -> SemanticEvent:
        import logging as _log
        _bus_log = _log.getLogger(__name__)
        self._seq += 1
        event.seq = self._seq
        # ---- 中间件链 (异常隔离) ----
        for mw in self._middlewares:
            if event is None:
                break  # 已被前面的中间件拦截, 停止传播
            try:
                event = mw(event)
            except Exception as exc:  # noqa: BLE001
                if len(self._middleware_errors) < self._MAX_ERRORS:
                    self._middleware_errors.append({
                        "middleware": getattr(mw, "__name__", str(mw)),
                        "error": str(exc),
                        "event_kind": str(event.kind) if event is not None else "?",
                        "ts": time.time(),
                    })
                _bus_log.debug("SemanticBus middleware %s raised, skipping", mw, exc_info=True)
        # 中间件返回 None = 拦截该事件, 不派发 handler (fail-closed)
        if event is None:
            self._blocked_count += 1
            return None
        self._history.append(event)
        # 历史上限: 超出后丢弃最旧的一半 (避免频繁 pop)
        if len(self._history) > self._MAX_HISTORY:
            self._history = self._history[len(self._history) // 2:]
        self._published_count += 1
        # ---- 精确订阅 + 通配订阅, 每个 handler 异常隔离 ----
        def _run_handler(h: Callable, event_kind: str) -> None:
            hname = getattr(h, "__name__", str(h))
            try:
                h(event)
                self._handler_call_counts[hname] = self._handler_call_counts.get(hname, 0) + 1
            except Exception as exc:  # noqa: BLE001
                if len(self._handler_errors) < self._MAX_ERRORS:
                    self._handler_errors.append({
                        "handler": hname, "event_kind": event_kind,
                        "error": str(exc), "ts": time.time(),
                    })
                _bus_log.debug("SemanticBus handler %s raised, continuing", h, exc_info=True)

        for h in self._handlers.get(str(event.kind), []):
            _run_handler(h, str(event.kind))
        for h in self._handlers.get("*", []):
            _run_handler(h, "*")
        return event

    def history(self) -> List[SemanticEvent]:
        return list(self._history)

    def stats(self) -> Dict[str, Any]:
        """返回总线运行统计快照。"""
        return {
            "published": self._published_count,
            "handler_errors": len(self._handler_errors),
            "middleware_errors": len(self._middleware_errors),
            "history_size": len(self._history),
            "subscriber_kinds": list(self._handlers.keys()),
            "subscriber_counts": {k: len(v) for k, v in self._handlers.items()},
            "middleware_count": len(self._middlewares),
            "handler_call_counts": dict(self._handler_call_counts),
        }

    def stats_snapshot(self) -> Dict[str, Any]:
        """返回原子快照 (包含 handler 调用计数的深拷贝)。"""
        s = self.stats()
        s["handler_call_counts"] = dict(self._handler_call_counts)
        return s

    def subscribe_count(self, kind: "EventSemantics | str") -> int:
        """返回指定语义的订阅者数量。"""
        key = kind.value if isinstance(kind, EventSemantics) else str(kind)
        return len(self._handlers.get(key, []))

    def recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """返回最近 N 条 handler/middleware 错误。"""
        all_errs = self._handler_errors + self._middleware_errors
        all_errs.sort(key=lambda e: e.get("ts", 0), reverse=True)
        return all_errs[:limit]

    def clear_errors(self) -> None:
        """清空错误日志。"""
        self._handler_errors.clear()
        self._middleware_errors.clear()

    def clear_history(self) -> None:
        """清空事件历史 (不影响统计计数)。"""
        self._history.clear()


# ============================================================ 可插拔 Agent Loop
@dataclass
class LoopContext:
    """传给 Agent Loop 的运行上下文。"""

    goal: str
    agent_id: str = "main"
    parent_id: Optional[str] = None
    tools: List[str] = field(default_factory=list)
    model: str = "default"
    max_steps: int = 12
    # 模型决策函数 (注入式, 便于测试与替换真模型):
    #   输入 observation_history: List[SemanticEvent], 返回决策 dict
    decide_fn: Optional[Callable[[List[SemanticEvent]], Dict[str, Any]]] = None
    # 工具执行函数: 输入 (name, args) -> result str
    execute_fn: Optional[Callable[[str, Dict[str, Any]], str]] = None
    # 权限门: 输入 ToolCall -> (allowed: bool, reason: str)
    gate_fn: Optional[Callable[["ToolCall"], tuple[bool, str]]] = None
    state: Dict[str, Any] = field(default_factory=dict)
    bus: Optional[SemanticBus] = None


@dataclass
class LoopResult:
    success: bool
    steps: int
    final_answer: str
    transcript: List[SemanticEvent] = field(default_factory=list)
    cost_tokens: int = 0


class BaseAgentLoop:
    """Agent Loop 基类; 所有循环策略继承并实现 run()。"""

    name: str = "base"

    def __init__(self, bus: Optional[SemanticBus] = None) -> None:
        self.bus = bus or SemanticBus()

    def emit(self, kind: EventSemantics, source: str, payload: Dict[str, Any]) -> SemanticEvent:
        return self.bus.publish(SemanticEvent(kind=kind, source=source, payload=payload))

    def run(self, ctx: LoopContext) -> LoopResult:  # pragma: no cover - 抽象
        raise NotImplementedError


# ============================================================ [已弃用] arch 层 LoopRegistry / ReActLoop
# 这些是 arch 层的轻量测试工具, 与 core.loop_provider 的同名类接口不同:
#   - arch: LoopRegistry (class-level dict) + ReActLoop (extends BaseAgentLoop, run(ctx: LoopContext))
#   - core: LoopRegistry (instance-level dict) + ReActLoop (extends LoopProvider, run_loop(agent, user_input))
# 生产环境请使用 core.loop_provider 的版本; arch 版本仅用于单元测试和原型验证。
import warnings as _warnings


class LoopRegistry:
    """[已弃用] arch 层 Loop 注册表 (class-level dict, 仅测试用)。

    .. deprecated:: 0.2.015
        生产环境请使用 core.loop_provider.LoopRegistry (实例级 dict, 支持 config 选择)。
        此类保留仅供 arch 层单元测试和 LoopProviderBridge 原型验证。
    """

    _registry: Dict[str, type[BaseAgentLoop]] = {}

    @classmethod
    def register(cls, name: str, loop_cls: type[BaseAgentLoop]) -> None:
        cls._registry[name] = loop_cls

    @classmethod
    def get(cls, name: str) -> type[BaseAgentLoop]:
        if name not in cls._registry:
            raise KeyError(f"未知 Agent Loop: {name}; 可用: {cls.names()}")
        return cls._registry[name]

    @classmethod
    def names(cls) -> List[str]:
        return list(cls._registry)

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()


class ReActLoop(BaseAgentLoop):
    """[已弃用] arch 层 ReAct 循环 (仅测试用)。

    .. deprecated:: 0.2.015
        生产环境请使用 core.loop_provider.ReActLoop。
        此类保留仅供 arch 层单元测试 (直接调用 run(ctx: LoopContext))。
    """

    name = "react"

    def run(self, ctx: LoopContext) -> LoopResult:
        if ctx.decide_fn is None or ctx.execute_fn is None:
            raise ValueError("ReActLoop 需要 decide_fn 与 execute_fn")
        history: List[SemanticEvent] = []
        self.emit(EventSemantics.DECIDE, ctx.agent_id, {"phase": "plan", "goal": ctx.goal})
        answer = ""
        for step in range(1, ctx.max_steps + 1):
            decision = ctx.decide_fn(history)
            act = decision.get("action", "finish")
            if act == "finish" or decision.get("finish"):
                answer = decision.get("answer", "")
                self.emit(EventSemantics.TERMINATE, ctx.agent_id,
                          {"reason": "model-finished", "answer": answer})
                break
            tool = decision.get("tool", "")
            args = decision.get("args", {})
            self.emit(EventSemantics.EMIT, ctx.agent_id,
                      {"tool": tool, "args": args, "step": step})
            obs = ctx.execute_fn(tool, args)
            ev_obs = self.emit(EventSemantics.OBSERVE, ctx.agent_id,
                               {"tool": tool, "observation": obs, "step": step})
            history.append(ev_obs)
            self.emit(EventSemantics.REFLECT, ctx.agent_id,
                      {"step": step, "ok": not str(obs).startswith("ERROR")})
        if not answer:
            answer = "(达到最大步数, 未显式 finish)"
        return LoopResult(success=True, steps=len(history), final_answer=answer, transcript=history)


# 注册默认 Loop (供测试与 arch 层使用; 生产环境由 core.loop_plugin.LoopPlugin 注册)
LoopRegistry.register("react", ReActLoop)


# ============================================================ 工具管线 waterfall
@dataclass
class ToolCall:
    name: str
    args: Dict[str, Any]
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class ToolOutcome:
    call_id: str
    name: str
    ok: bool
    result: str = ""
    error: str = ""
    elapsed: float = 0.0
    executed: bool = False          # 是否真正进入执行阶段 (被 gate/hook 拦截则为 False)
    pre_notes: List[str] = field(default_factory=list)
    post_notes: List[str] = field(default_factory=list)


class ToolPipeline:
    """工具执行 waterfall: pre-execute -> gate -> execute -> post-execute -> audit。

    阶段:
      pre    自定义前置 stage (可改写/拦截 call)
      gate   权限门 (ctx.gate_fn): 不被允许则短路
      execute 真正执行
      post   自定义后置 stage (可改写/记录 result)
      audit  发射内核事件 tool.executed + 语义 OBSERVE
    """

    def __init__(self, hooks=None, bus: Optional[SemanticBus] = None,
                 agent_id: str = "main") -> None:
        self.pre_stages: List[Callable[[ToolCall, LoopContext], ToolCall]] = []
        self.post_stages: List[Callable[[ToolCall, ToolOutcome, LoopContext], ToolOutcome]] = []
        self.hooks = hooks  # 既有 HookManager (可选)
        self.bus = bus
        self.agent_id = agent_id

    def add_pre_stage(self, fn: Callable[[ToolCall, LoopContext], ToolCall]) -> None:
        self.pre_stages.append(fn)

    def add_post_stage(self, fn: Callable[[ToolCall, ToolOutcome, LoopContext], ToolOutcome]) -> None:
        self.post_stages.append(fn)

    def run(self, call: ToolCall, ctx: LoopContext) -> ToolOutcome:
        started = time.time()
        outcome = ToolOutcome(call_id=call.call_id, name=call.name, ok=False)

        # ---- pre-execute waterfall ----
        for stage in self.pre_stages:
            try:
                call = stage(call, ctx)
            except Exception as exc:  # 前置 stage 失败 = fail-closed 拦截
                outcome.error = f"pre-stage-blocked: {exc}"
                self._audit(outcome, ctx, started)
                return outcome
            outcome.pre_notes.append(f"pre:{getattr(stage, '__name__', 'stage')}")
        # 既有 HookManager 的 pre execute
        if self.hooks is not None:
            try:
                self.hooks.run_pre("tool", {"name": call.name, "args": call.args})
                outcome.pre_notes.append("hook:pre")
            except Exception as exc:  # hooks 阻塞/失败 -> 视为拒绝
                outcome.error = f"hook-pre-blocked: {exc}"
                self._audit(outcome, ctx, started)
                return outcome

        # ---- gate (权限门) ----
        if ctx.gate_fn is not None:
            allowed, reason = ctx.gate_fn(call)
            if not allowed:
                outcome.error = f"gate-denied: {reason}"
                self._audit(outcome, ctx, started)
                return outcome

        # ---- execute ----
        try:
            if ctx.execute_fn is None:
                raise RuntimeError("context 缺少 execute_fn")
            result = ctx.execute_fn(call.name, call.args)
            outcome.result = result
            outcome.ok = not str(result).startswith("ERROR")
            outcome.executed = True
        except Exception as exc:
            outcome.error = f"{type(exc).__name__}: {exc}"
            outcome.ok = False
            outcome.executed = True  # 已真正进入执行 (只是抛错)

        # ---- post-execute waterfall ----
        for stage in self.post_stages:
            outcome = stage(call, outcome, ctx)
            outcome.post_notes.append(f"post:{getattr(stage, '__name__', 'stage')}")
        if self.hooks is not None and outcome.ok:
            try:
                self.hooks.run_post("tool", {"name": call.name, "result": outcome.result})
                outcome.post_notes.append("hook:post")
            except Exception:
                pass

        self._audit(outcome, ctx, started)
        return outcome

    def _audit(self, outcome: ToolOutcome, ctx: LoopContext, started: float) -> None:
        outcome.elapsed = time.time() - started
        if self.bus is not None:
            if outcome.executed:
                # OBSERVE 语义 = "环境返回了观察" (工具真正执行过)
                self.bus.publish(SemanticEvent(
                    kind=EventSemantics.OBSERVE, source=self.agent_id,
                    payload={"tool": outcome.name, "ok": outcome.ok,
                             "result": outcome.result[:2000], "error": outcome.error,
                             "elapsed": outcome.elapsed},
                ))
            elif outcome.error:
                # 被 gate/hook 拦截: 用 DECIDE 语义记录安全决策 (不产生 OBSERVE)
                self.bus.publish(SemanticEvent(
                    kind=EventSemantics.DECIDE, source=self.agent_id,
                    payload={"tool": outcome.name, "action": "blocked",
                             "reason": outcome.error, "elapsed": outcome.elapsed},
                ))
        # 也尽量发射内核事件 (若 ctx 带了 kernel)
        kernel = ctx.state.get("kernel")
        if kernel is not None:
            try:
                kernel.emit("tool.executed", {
                    "name": outcome.name, "status": "ok" if outcome.ok else "err",
                    "elapsed": outcome.elapsed, "error": outcome.error,
                })
            except Exception:
                pass


class LoopProviderBridge:
    """把 arch.execution.BaseAgentLoop 桥接为 core.loop_provider.LoopProvider。

    让 arch 层的轻量可测试 Loop 能直接用于 Agent 主循环:

        from qingxiaotuan.arch.execution import LoopProviderBridge, BaseAgentLoop, SemanticBus
        class MyLoop(BaseAgentLoop):
            name = "my-loop"
            def run(self, ctx): ...
        bridge = LoopProviderBridge(MyLoop(SemanticBus()))
        kernel.provide("loop_provider", bridge)

    桥接策略:
    - run_loop() 构造 LoopContext(decide_fn/execute_fn 从 Agent 懒提取), 调 BaseAgentLoop.run()
    - observe history 被正确传递给 arch loop 的 decide_fn, 让 arch loop 能感知工具执行结果
    - 回调 on_token/on_tool/on_tool_result 在 SemanticBus 上发布事件
    - on_error 收集所有异常, 最终返回错误摘要
    - 结果文本直接返回给 Agent 作为 answer
    """

    name: str
    description: str
    should_nudge: Any  # callable, 由 LoopProvider 契约要求

    def __init__(self, inner: BaseAgentLoop) -> None:
        self._inner = inner
        self.name = inner.name
        self.description = getattr(inner, "description", f"arch-loop:{inner.name}")
        self.should_nudge = lambda agent, interval=3: False  # 默认不蒸馏
        self._error_count = 0
        self._last_errors: List[str] = []

    @property
    def error_count(self) -> int:
        return self._error_count

    def run_loop(self, agent, user_input, *, stream=True, on_token=None,
                 on_tool=None, on_reason=None, on_tool_result=None,
                 on_error=None, max_iterations=None, session_id=None) -> str:
        max_steps = max_iterations or int(agent.config.get("agent.max_iterations", 20))
        self._error_count = 0
        self._last_errors = []

        # ---- 内部状态: 跨步观察历史 ----
        _observe_history: List[Dict[str, Any]] = []  # [{tool, result, ok}, ...]

        # ---- decide_fn: 把 agent 模型调用包装成 arch.decide_fn 签名 ----
        def _decide(history: list) -> dict:
            """把 Agent 模型调用包装为 arch.decide_fn。

            输入 history 是 arch loop 累积的 SemanticEvent 列表 (observe 事件);
            同时也维护 _observe_history 供后续步骤参考。
            """
            # 构造 messages: agent 已有 messages (含 system + 历史 tool 消息)
            # 追加最新用户输入 (如果不在末尾)
            messages = list(agent.messages)
            if not messages or messages[-1].get("content") != user_input:
                messages.append({"role": "user", "content": user_input})
            # 调用模型
            try:
                result = agent._chat_with_retry(
                    messages,
                    tools=agent.registry.schemas(
                        tool_set=agent.effective_tool_set(),
                        exclude_tools=agent.exclude_tools),
                    stream=stream, on_token=on_token, on_reason=on_reason,
                )
            except Exception as exc:
                self._error_count += 1
                err_msg = f"[模型错误] {exc}"
                self._last_errors.append(err_msg)
                if on_error:
                    on_error(str(exc))
                return {"action": "finish", "answer": err_msg}
            if result is None:
                return {"action": "finish", "answer": ""}
            # 提取 tool_calls
            if hasattr(result, "content"):
                msg_dict = {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {"id": tc.id, "function": {"name": tc.name, "arguments": tc.arguments}}
                        for tc in (result.tool_calls or [])
                    ],
                }
            elif isinstance(result, dict):
                message = result.get("message", result)
                msg_dict = message if isinstance(message, dict) else {"role": "assistant", "content": str(message)}
            else:
                msg_dict = {"role": "assistant", "content": str(result)}
            agent.messages.append(msg_dict)
            agent._session_append("assistant", message=msg_dict)
            # 无 tool_calls -> 终止
            tc_list = msg_dict.get("tool_calls", [])
            if not tc_list:
                return {"action": "finish", "answer": msg_dict.get("content", "")}
            # 有 tool_calls -> 执行第一个 (简化: arch Loop 单工具/步)
            tc = tc_list[0]
            return {
                "action": "tool",
                "tool": tc["function"]["name"],
                "args": tc["function"].get("arguments", "{}"),
            }

        # ---- execute_fn: 通过 agent._execute_tools 执行 (保留安全护栏/账本/HookManager) ----
        def _execute(name: str, args) -> str:
            """执行工具调用, 委托给 agent._execute_tools 以保留完整安全管线。

            关键: 不再直接调用 registry.dispatch(), 而是构造 tool_call dict
            交给 agent._execute_tools(), 确保:ToolPipeline 5 阶段、HookManager、
            事务账本、MCP 安全加固全部生效。
            """
            if on_tool:
                on_tool(name, str(args))
            # 构造 agent._execute_tools 期望的 tool_call dict 格式
            import uuid as _uuid
            call_id = f"bridge-{_uuid.uuid4().hex[:8]}"
            args_str = args if isinstance(args, str) else (
                json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args))
            tool_call_dict = {
                "id": call_id,
                "function": {"name": name, "arguments": args_str},
            }
            # 记录执行前的消息数, 用于提取工具结果
            n_before = len(agent.messages)
            try:
                agent._execute_tools(
                    [tool_call_dict],
                    on_tool=on_tool,
                    on_tool_result=on_tool_result,
                )
            except Exception as exc:
                self._error_count += 1
                result = f"[错误] {name}: {exc}"
                self._last_errors.append(result)
                _observe_history.append({"tool": name, "result": result, "ok": False})
                return result
            # 从 agent.messages 中提取刚产生的 tool 结果消息
            result = ""
            for m in agent.messages[n_before:]:
                if m.get("role") == "tool" and m.get("tool_call_id") == call_id:
                    result = m.get("content", "")
                    break
            if not result:
                # 兜底: 取最后一条 tool 消息
                for m in reversed(agent.messages[n_before:]):
                    if m.get("role") == "tool":
                        result = m.get("content", "")
                        break
            ok = not str(result).startswith("[错误]") and not str(result).startswith("[拒绝")
            # 记录观察历史 (供 arch loop decide_fn 的 history 参数使用)
            _observe_history.append({"tool": name, "result": str(result), "ok": ok})
            # 发布 SemanticEvent (OBSERVE)
            if self._inner.bus is not None:
                self._inner.bus.publish(SemanticEvent(
                    kind=EventSemantics.OBSERVE,
                    source="bridge",
                    payload={"tool": name, "ok": ok, "result": str(result)[:500]},
                ))
            return str(result)

        # ---- 执行 arch Loop ----
        from . import LoopContext as _LoopContext  # noqa: F811
        ctx = _LoopContext(
            goal=user_input,
            agent_id="main",
            decide_fn=_decide,
            execute_fn=_execute,
            max_steps=max_steps,
            bus=self._inner.bus,
        )
        # 生命周期钩子: 在 arch loop 执行前后触发
        self.on_task_start(agent, user_input)
        result = self._inner.run(ctx)
        self.on_task_end(agent, result.final_answer, result.steps)
        return result.final_answer

    def on_task_start(self, agent, user_input) -> None:
        """任务开始: 记录到 session + 重置统计。"""
        self._error_count = 0
        self._last_errors = []
        try:
            agent._session_append("task.start", goal=user_input, loop=self.name)
        except Exception:  # noqa: BLE001
            pass

    def on_task_end(self, agent, answer, iteration) -> None:
        """任务结束: 记录结果与统计到 session。"""
        try:
            agent._session_append(
                "task.end",
                answer_preview=(answer[:200] + "...") if answer and len(answer) > 200 else (answer or ""),
                iterations=iteration,
                errors=self._error_count,
                error_summary=self._last_errors[-3:] if self._last_errors else [],
                loop=self.name,
            )
        except Exception:  # noqa: BLE001
            pass

    def stats(self) -> Dict[str, Any]:
        """返回桥接运行统计。"""
        return {
            "loop_name": self.name,
            "error_count": self._error_count,
            "last_errors": list(self._last_errors[-5:]),
        }



