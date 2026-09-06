"""Agent 主循环 —— ReAct: think -> tool -> observe -> think ... 直到完成。

融合点:
- Harness: 循环本身也是一个挂在微内核上的组件, 每个 step 都发出内核事件
  并写入 append-only 会话事件流 (可审计、可回放)。
- Hermes: 每隔 skill_nudge_interval 个 turn, 循环会"自我提醒"反思 —
  这次的方法值不值得蒸馏成技能? 这就是自进化闭环的触发器。
- Claude Code: 大上下文机制 —— 系统提示里钉一份代码库地图, 长会话里按
  优先级智能压缩旧历史 (ContextManager), 关键信息不丢。

重构说明 (v0.3):
- 模型路由 → core/model_router.py (AgentModelRouter)
- 韧性组件 → core/resilience.py (AgentResilience)
- 可观测性 → core/observability.py (AgentObservability)
- Agent 本体只保留 ReAct 循环 + 组合委托
"""

from __future__ import annotations

import inspect
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from ..config import Config
from ..context.manager import ContextManager, estimate_messages
from .kernel import Kernel
from .model_router import AgentModelRouter
from .resilience import AgentResilience
from .observability import AgentObservability
from .retry import classify_error as _classify_error, retry_after_seconds as _retry_after_seconds
from .prompts import build_system_prompt, build_task_context, is_short_task
from .tool_executor import ToolExecutor
from .auto_route import AutoRouter, RouteSession, tool_messages_of_turn
from .agent_helpers import (
    SKILL_NUDGE, summarize_messages, should_nudge,
    detect_verify_write_tools,
)
from .loop_provider import LoopProvider, ReActLoop
from .agent_loop_fusion import (
    classify_transient,
    resolve_max_iterations,
    retry_step,
)
from .agent_goal import GoalMixin
from .agent_vision import VisionMixin
from ..tools.base import ToolContext, ToolResult
from ..vision import encode_image_source, build_user_content, build_tool_content
from ..vision.blocks import ImageRef

log = logging.getLogger(__name__)


class Agent(GoalMixin, VisionMixin):
    def __init__(
        self,
        kernel: Kernel,
        config: Config,
        workspace: str,
        confirm: Optional[Callable[[str], bool]] = None,
        exclude_tools: tuple = (),
        indexer=None,
        context_manager: Optional[ContextManager] = None,
        system_extra: str = "",
    ) -> None:
        self.kernel = kernel
        self.config = config
        self.workspace = workspace
        self.model = kernel.require("model_adapter")
        self.registry = kernel.require("tool_registry")
        self.session = kernel.get("session_store")
        self.messages: List[Dict[str, Any]] = []
        self.turn_count = 0
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self._cancel_event = threading.Event()
        self.exclude_tools: Set[str] = set(exclude_tools or ())
        # 子代理角色指令 (AgentType.system_extra): 追加到系统提示末尾 (空串则无感)
        self.system_extra = system_extra or ""
        self.yolo = config.is_yolo()
        self.plan_mode = False  # Plan Mode (对标 Claude Code: 只读模式, 不修改文件)
        # Goal 模式 (对标 Claude Code /goal): 设定完成条件, 循环直到满足
        self._goal: Optional[str] = None  # 目标描述 (None = 未启用 Goal)
        self._goal_check_prompt: Optional[str] = None  # 条件检查提示 (让快速模型判断是否满足)
        self.ctx = ToolContext(kernel=kernel, workspace=workspace, confirm=confirm, yolo=self.yolo)
        self.ctx.plan_mode = self.plan_mode
        # 同一列表对象: checkpoint 工具经 ctx.conversation 截断对话流 (rewind)
        self.ctx.conversation = self.messages
        # 反向引用: 让工具 handler 能同步 agent 级状态 (如 enter/exit_plan_mode 同步 plan_mode)
        self.ctx.agent = self
        # 多模态: 用户挂接的图片缓冲 (一次性, 随下一轮 user 消息发送后清空)
        self.pending_images: List[ImageRef] = []
        # 当前生效的工具集名 (Plan/YOLO/Standard), 供主循环取用对应工具清单
        self._tool_set: str = self._compute_tool_set()
        # --- 提取的组件 ---
        # 模型路由器 (core/model_router.py): 自动选择/切换模型
        self._router = AgentModelRouter(config, kernel)
        # 规划/执行 + 卡住升级的跨 turn 路由状态 (core/auto_route)
        self._auto = AutoRouter(self.config)
        self._route_session = RouteSession()
        # 韧性组件 (core/resilience.py): 重试/限流/熔断
        self._resilience = AgentResilience(config, kernel)
        # 可观测性组件 (core/observability.py): 遥测/用量/成本
        self._obs = AgentObservability(config, self.config.home)

        # 工具执行器 (从 Agent 拆出的独立组件)
        self._tool_executor = ToolExecutor(
            registry=self.registry,
            messages=self.messages,
            session_append=self._session_append,
            tool_content_fn=self._tool_content,
            telemetry=self._obs.telemetry,
            trace_id=self._obs.trace_id,
        )

        # 大上下文: 把代码库地图钉进系统提示 (延迟构建, 避免启动被 Ctrl+C 打断)
        codebase_map_text = ""
        if indexer is not None and config.get("context.pin_codebase", True):
            try:
                codebase_map_text = indexer.build().map_text()
            except KeyboardInterrupt:
                # 用户想取消初始化, 优雅退出而非 Traceback
                codebase_map_text = ""
            except Exception as exc:  # noqa: BLE001
                log.debug("codebase 地图构建失败, 系统提示不含地图: %s", exc)
                codebase_map_text = ""

        self._codebase_map = codebase_map_text
        self._system_prompt = self._build_system(task_hint="")

        # 上下文优先级管理器
        if context_manager is not None:
            self.context_manager = context_manager
        else:
            self.context_manager = ContextManager(
                keep_recent=config.get("context.keep_recent", 14),
                budget_tokens=config.get("context.budget_tokens", 60000),
                compact_trigger=config.get("context.compact_trigger", None),
                strategy=config.get("context.compact_strategy", "smart"),
                summarize=self._summarize,
            )

        # 可插拔 Loop: 从 kernel 读取 LoopProvider, 无则用默认 ReAct
        self._loop_provider: LoopProvider = kernel.get("loop_provider") or ReActLoop()


    def _build_system(self, task_hint: str = "") -> str:
        """组装「绝对稳定」的系统提示 (不含随任务变化的语义召回)。"""
        prompt = build_system_prompt(
            home=self.config.home,
            workspace=self.workspace,
            memory_store=self.kernel.get("memory_store"),
            skill_manager=self.kernel.get("skill_manager") if self.config.get("skills.auto_inject", True) else None,
            skill_limit=self.config.get("skills.inject_limit", 3),
            codebase_map=self._codebase_map,
            reply_language=self.config.get("language", "") or "zh-CN",
            output_style=self.config.get("ui.output_style", "default"),
        )
        # 类型化子代理的角色指令 (task 工具的 AgentType.system_extra)
        if self.system_extra:
            prompt += "\n\n" + self.system_extra
        return prompt

    # ------------------------------------------------------------ 会话事件流

    _CS_ENABLED = True  # 跨会话引用开关 (可用 config 关闭)

    def _resolve_cross_refs(self, text: str) -> str:
        """把用户输入里的 @session:<id> / @#<n> 引用展开为可读上下文 (只读注入)。

        解析失败不抛异常: 返回原文并用错误块提示。
        """
        if not self._CS_ENABLED:
            return text
        try:
            from .cross_session import CrossSessionResolver
            res = CrossSessionResolver(
                home=self.config.home,
                session_store=self.session,
            )
            return res.inject(text)
        except Exception as exc:  # noqa: BLE001
            log.debug("跨会话引用解析失败, 原样透传: %s", exc)
            return text

    def _session_append(self, event_type: str, **payload: Any) -> None:
        """把事件写入 append-only 会话流 (可审计、可回放)。"""
        if self.session is not None:
            try:
                self.session.append(event_type, payload)
            except Exception as exc:  # noqa: BLE001
                log.debug("会话事件写入失败 (%s): %s", event_type, exc)

    # ------------------------------------------------------------ 用量统计

    def _accumulate_usage(self, usage: Any) -> None:
        """累计模型用量 —— 委托给 AgentObservability。"""
        self._obs.accumulate_usage(usage, session_append=self._session_append,
                                   config=self.config, turn_count=self.turn_count)
        # 同步 total_usage 到 Agent 实例 (供外部读取)
        self.total_usage = self._obs.total_usage

    def _estimate_total_cost(self) -> float:
        """估算当前会话总花费 (USD) —— 委托给 AgentObservability。"""
        obs = getattr(self, '_obs', None)
        if obs is not None:
            return float(obs.estimate_total_cost())
        # Agent.__new__ / 鸭子类型对象 (测试或序列化重建) 可能没有 _obs:
        # 直接用 token 用量估算, 保持成本报告可用。
        from ..core.agent_helpers import estimate_total_cost as _etc
        return _etc(getattr(self, "config", None), getattr(self, "total_usage", {}) or {})

    def cache_hit_rate(self) -> Optional[float]:
        """DeepSeek 前缀缓存命中率 —— 委托给 AgentObservability。"""
        return self._obs.cache_hit_rate()

    # ------------------------------------------------------------ 技能蒸馏 (Hermes 闭环)

    @property
    def _nudge_interval(self) -> int:
        return int(self.config.get("agent.skill_nudge_interval", 3))

    def _should_nudge(self) -> bool:
        return should_nudge(self.turn_count, self._nudge_interval)

    def _nudge_round(self, on_token=None, on_reason=None, on_error=None) -> None:
        """任务推进数轮后自我提醒: 本次方法是否值得蒸馏成技能。

        模型若决定蒸馏, 会返回 skill_save 工具调用, 这里执行之; 若回复空内容
        表示无需蒸馏, 直接结束。nudge 是单轮收尾, 不再继续循环。
        """
        self.messages.append({"role": "user", "content": SKILL_NUDGE})
        try:
            result = self._chat_with_retry(
                self.messages,
                tools=self._schemas(),
                stream=False,
                on_token=on_token,
                on_reason=on_reason,
            )
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(str(exc))
            return
        if result is None:
            return
        usage = getattr(result, "usage", None)
        if usage:
            self._accumulate_usage(usage)
        msg_dict = self._parse_assistant_message(result)
        self.messages.append(msg_dict)
        self._session_append("assistant", message=msg_dict)
        tool_calls = msg_dict.get("tool_calls", [])
        if tool_calls:
            self._tool_executor.execute_batch(
                tool_calls, self.ctx,
                exclude_tools=self.exclude_tools,
            )

    # ------------------------------------------------------------ 主循环

    def cancel(self) -> None:
        """请求在当前模型调用或工具步骤结束后停止当前任务。"""
        self._cancel_event.set()

    def clear_cancel(self) -> None:
        """清除取消请求，准备执行下一项任务。"""
        self._cancel_event.clear()

    # ------------------------------------------------------------ Goal 模式

    def _chat_with_retry(self, messages, tools, stream, on_token, on_reason, label="模型"):
        """带超时/重试/熔断的模型调用 —— 委托给 AgentResilience + AgentObservability。

        测试可能用 Agent.__new__ 绕过 __init__, 此时 _resilience 不存在,
        回退到使用 self.retry_policy / self._rate_limiter 直连 (保持旧行为)。
        """
        # 正常路径: 委托给韧性组件
        resilience = getattr(self, '_resilience', None)
        obs = getattr(self, '_obs', None)

        started_at = time.monotonic()
        span = obs.start_span("model.chat", attributes={"label": label}) if obs is not None else None

        try:
            if resilience is not None:
                result = resilience.chat_with_retry(
                    self.model, messages, tools, stream, on_token, on_reason, label,
                )
            else:
                # Agent.__new__ 回退: 使用直接设置的 retry_policy / _rate_limiter
                retry_policy = getattr(self, 'retry_policy', None)
                rate_limiter = getattr(self, '_rate_limiter', None)
                def _direct_call():
                    if rate_limiter is not None:
                        rate_limiter.acquire()
                    try:
                        return self.model.chat(
                            messages, tools=tools, stream=stream,
                            on_token=on_token, on_reason=on_reason,
                        )
                    finally:
                        if rate_limiter is not None:
                            rate_limiter.release()
                if retry_policy is not None:
                    result = retry_policy.call(_direct_call, label=label)
                else:
                    result = _direct_call()
        except Exception as exc:  # noqa: BLE001
            if obs is not None and span is not None:
                obs.finish_span(span.span_id, status="error")
                obs.add_span_event(span, "model.error", {"error": str(exc)})
            raise
        if obs is not None and span is not None:
            elapsed_ms = (time.monotonic() - started_at) * 1000
            obs.finish_span(span.span_id, status="ok")
            usage = getattr(result, "usage", None) if result is not None else None
            tokens = 0
            if usage:
                pt = usage.get("prompt_tokens") if isinstance(usage, dict) else getattr(usage, "prompt_tokens", 0)
                ct = usage.get("completion_tokens") if isinstance(usage, dict) else getattr(usage, "completion_tokens", 0)
                tokens = int(pt or 0) + int(ct or 0)
            breaker = None
            if resilience is not None:
                breaker = resilience._circuit_breaker
            if breaker is not None and breaker.enabled:
                obs.add_span_event(span, "circuit_breaker", {"state": breaker.state})
            obs.record_model_call(elapsed_ms, tokens, success=True)
        return result

    # ------------------------------------------------------------ 自动模型路由

    def _maybe_route_model(self, task: str, override: int = 0, has_images: Optional[bool] = None) -> None:
        """按 router 配置自动选择/切换模型 —— 委托给 AgentModelRouter。"""
        self._router.maybe_route(task, override=override, has_images=has_images,
                                current_model=self.model, pending_images=self.pending_images)
        # 路由可能切换了模型, 同步到当前实例
        if self._router.last_route and self._router.last_route.get("switch"):
            self.model = self.kernel.require("model_adapter")

    # ------------------------------------------------------------ 工具执行 (委托 ToolExecutor)

    def _execute_tools(
        self,
        tool_calls: List[Dict[str, Any]],
        on_tool: Optional[Callable[[str, str], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """执行一批工具调用 —— 委托给 ToolExecutor (core/tool_executor.py)。

        同时把工具调用/结果以规范事件 (tool_call / tool_call_update) 落盘到会话流,
        供 Trajectory 重建与 replay 回放 (CLI 与 ACP/IDE 会话统一同一事件流)。
        """
        _counter = {"n": 0}

        def _ont(name: str, args: str) -> None:
            _counter["n"] += 1
            tid = f"t{_counter['n']}"
            self._session_append("tool_call", name=name, arguments=args, toolCallId=tid)
            if on_tool:
                on_tool(name, args)

        def _ontr(name: str, result: str) -> None:
            self._session_append(
                "tool_call_update", name=name, status="completed", result=result,
                toolCallId=f"t{_counter['n']}",
            )
            if on_tool_result:
                on_tool_result(name, result)

        self._tool_executor.execute_batch(
            tool_calls, self.ctx,
            exclude_tools=self.exclude_tools,
            on_tool=_ont,
            on_tool_result=_ontr,
        )

    # ------------------------------------------------------------ 编码验证闭环 (verify_loop)
    def _run_verify_loop(
        self,
        tool_calls: List[Dict[str, Any]],
        on_token: Optional[Callable[[str], None]] = None,
    ) -> None:
        """写工具执行后触发验证闭环 (core/verify_loop), 失败把错误喂回模型自修复。

        设计见 verify_loop.py: Agent 每次修改文件后自动跑 pytest/mypy/ruff, 失败则把
        错误信息作为一条 user 消息注入, 下一轮 while 循环里模型据此自我修复。最多
        max_heal_rounds 轮 (实际由调用方 max_iterations 兜底)。

        仅当 config.verify.enabled 且 verify.auto 为真时生效; 默认配置未配置 verify,
        故不影响既有运行行为 (避免无测试/无 mypy/ruff 的项目被误报刷屏)。
        """
        cfg = self.config.get("verify", {})
        if not isinstance(cfg, dict) or not cfg.get("enabled") or not cfg.get("auto", True):
            return
        if not detect_verify_write_tools(tool_calls):
            return
        try:
            from ..core.verify_loop import (
                MAX_HEAL_ROUNDS, VerifyConfig, verify_once,
            )
            checks = cfg.get("checks", {}) or {}
            vcfg = VerifyConfig(
                enabled=True,
                auto=True,
                max_heal_rounds=int(cfg.get("max_heal_rounds", MAX_HEAL_ROUNDS)),
                test_cmd=str(checks.get("test", "") or ""),
                typecheck_cmd=str(checks.get("typecheck", "") or ""),
                lint_cmd=str(checks.get("lint", "") or ""),
            )
            rnd = verify_once(self.workspace, vcfg)
        except Exception as exc:  # noqa: BLE001 - 验证基础设施异常不应阻断主流程
            log.debug("验证闭环执行失败 (已跳过): %s", exc)
            return
        if rnd.all_passed:
            if on_token:
                on_token("\n[验证闭环] 测试/类型检查/lint 全部通过 ✓\n")
            self._session_append("verify.passed", round=rnd.round_num)
            return
        # 有失败: 把错误摘要喂回模型, 触发自修复 (下一轮决策将看到此上下文)
        summary = rnd.summary()
        note = (
            "\n[验证闭环] 以下检查未通过, 请分析并修复后继续;\n"
            "修复后再次写入文件将重新触发验证:\n" + summary
        )
        if on_token:
            on_token(note)
        self.messages.append({"role": "user", "content": note})
        self._session_append("verify.failed", summary=summary)

    # ---- 向后兼容属性: 统一 fallback 辅助 ----
    # 测试可能用 Agent.__new__ 绕过 __init__, 直接设置这些属性。
    # property 只在 __init__ 完成后生效; __new__ 绕过时仍可直接赋值。
    # 统一用 _get_fallback / _set_fallback 消除重复的 if/else 模式。

    def _get_fallback(self, component: str, attr: str, fallback_key: str):
        """从子组件读取属性, 组件不存在时回退到 __dict__ fallback。"""
        comp = self.__dict__.get(component)
        if comp is not None:
            return getattr(comp, attr)
        return self.__dict__.get(fallback_key)

    def _set_fallback(self, component: str, attr: str, fallback_key: str, value):
        """向子组件设置属性, 组件不存在时写入 __dict__ fallback。"""
        comp = self.__dict__.get(component)
        if comp is not None:
            setattr(comp, attr, value)
        else:
            self.__dict__[fallback_key] = value

    @property
    def _last_route(self):
        return self._router.last_route

    @_last_route.setter
    def _last_route(self, v):
        self._router.last_route = v

    @property
    def _model_switcher(self):
        return self._router._model_switcher

    @_model_switcher.setter
    def _model_switcher(self, v):
        self._router._model_switcher = v

    @property
    def _circuit_breaker(self):
        return self._get_fallback('_resilience', '_circuit_breaker', '_cb_fallback')

    @_circuit_breaker.setter
    def _circuit_breaker(self, v):
        self._set_fallback('_resilience', '_circuit_breaker', '_cb_fallback', v)

    @property
    def _rate_limiter(self):
        return self._get_fallback('_resilience', '_rate_limiter', '_rl_fallback')

    @_rate_limiter.setter
    def _rate_limiter(self, v):
        self._set_fallback('_resilience', '_rate_limiter', '_rl_fallback', v)

    @property
    def _telemetry(self):
        return self._get_fallback('_obs', 'telemetry', '_tel_fallback')

    @_telemetry.setter
    def _telemetry(self, v):
        self._set_fallback('_obs', '_telemetry', '_tel_fallback', v)

    @property
    def _telemetry_trace(self):
        return self._get_fallback('_obs', 'trace_id', '_trace_fallback')

    @_telemetry_trace.setter
    def _telemetry_trace(self, v):
        self._set_fallback('_obs', 'trace_id', '_trace_fallback', v)

    @property
    def retry_policy(self):
        return self._get_fallback('_resilience', 'retry_policy', 'retry_policy')

    @retry_policy.setter
    def retry_policy(self, v):
        self._set_fallback('_resilience', 'retry_policy', 'retry_policy', v)

    @property
    def max_retries(self) -> int:
        return self._resilience.max_retries

    @max_retries.setter
    def max_retries(self, v) -> None:
        self._resilience.max_retries = int(v)

    @property
    def retry_backoff(self) -> float:
        return self._resilience.retry_backoff

    @retry_backoff.setter
    def retry_backoff(self, v) -> None:
        self._resilience.retry_backoff = float(v)

    @property
    def retry_jitter(self) -> float:
        return self._resilience.retry_jitter

    @retry_jitter.setter
    def retry_jitter(self, v) -> None:
        self._resilience.retry_jitter = float(v)

    @property
    def retry_on(self) -> set:
        return self._resilience.retry_on

    @retry_on.setter
    def retry_on(self, v) -> None:
        self._resilience.retry_on = set(v)

    @staticmethod
    def _classify(exc: Exception):
        return _classify_error(exc)

    @staticmethod
    def _retry_after(exc: Exception) -> Optional[float]:
        return _retry_after_seconds(exc)

    # ------------------------------------------------------ 用户级 Hooks

    def _hooks(self):
        """取用户级 HookManager (create_agent 时注入到 agent.ctx.hooks), 无则 None。"""
        ctx = getattr(self, "ctx", None)
        return getattr(ctx, "hooks", None) if ctx is not None else None

    def _notify_hook(self, event: str, payload: Dict[str, Any]) -> None:
        """触发通知类 hook (Stop/SubagentStop/PreCompact), 异常隔离、不阻断主流程。"""
        hooks = self._hooks()
        if hooks is None or not hooks.enabled_for(event):
            return
        try:
            hooks.run_notify(event, payload)
        except Exception as exc:  # noqa: BLE001
            log.debug("hook %s 执行失败: %s", event, exc)

    def _run_prompt_submit_hooks(self, prompt: str) -> str:
        """UserPromptSubmit hook: 返回注入本回合的附加上下文 (无则空串)。"""
        hooks = self._hooks()
        if hooks is None or not hooks.enabled_for("UserPromptSubmit"):
            return ""
        try:
            return hooks.run_user_prompt_submit(prompt) or ""
        except Exception as exc:  # noqa: BLE001
            log.debug("UserPromptSubmit hook 执行失败: %s", exc)
            return ""

    # ------------------------------------------------------------ 主循环

    def effective_tool_set(self) -> str:
        """当前生效的工具集名 (plan/yolo/standard), 与 plan_mode/yolo 实时同步。

        主循环据此向模型提供对应模式的工具清单 —— Plan 只给只读工具,
        YOLO/Standard 给全量工具 (危险操作的批准策略由权限闸门控制)。
        """
        self._tool_set = self._compute_tool_set()
        return self._tool_set

    def _compute_tool_set(self) -> str:
        if self.plan_mode:
            return "plan"
        if self.yolo:
            return "yolo"
        return "standard"

    def _schemas(self):
        """取传给模型的工具清单 (按 effective_tool_set 过滤)。

        对 registry.schemas 做接口兼容: 若它接受 tool_set / exclude_tools 关键字
        (真实 ToolRegistry / 测试双), 则透传; 否则极简 registry (仅接受无参调用)
        退化为裸调, 保证主循环总能拿到 schema 列表而不会因 TypeError 中断模型调用。
        详见 test_tool_set_wiring (要求透传 tool_set) 与 test_router/test_vision
        (极简 fake 只接受 schemas())。这确保主循环能正常进入模型轮并推进转台切换。
        """
        sch = getattr(self.registry, "schemas", None)
        if sch is None:
            return []
        try:
            sig = inspect.signature(sch)
        except (TypeError, ValueError):
            sig = None  # 无法反射签名 → 保守透传 (真实 registry 支持关键字)
        if sig is None or any(
            p.name == "tool_set" or p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        ):
            return sch(
                tool_set=self.effective_tool_set(),
                exclude_tools=self.exclude_tools,
            )
        # 极简 registry (如测试双 `def schemas(self)`) 不接受关键字 → 裸调
        return sch()

    # ---- 共享辅助: 消息解析 / 运行准备 / Goal / 收尾 ----

    @staticmethod
    def _parse_assistant_message(result) -> Dict[str, Any]:
        """把模型响应 (ModelResponse / dict / str) 转成标准 assistant 消息字典。

        统一入口: _nudge_round、_run_react_inline、LoopProvider 都需要此逻辑,
        消除 3 处重复的 isinstance 判定链。
        """
        if hasattr(result, "content"):
            return {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": [
                    {"id": tc.id, "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in (result.tool_calls or [])
                ],
            }
        if isinstance(result, dict):
            message = result.get("message", result)
            return (
                message
                if isinstance(message, dict)
                else {"role": "assistant", "content": str(message)}
            )
        return {"role": "assistant", "content": str(result)}

    def _prepare_run(
        self,
        user_input: str,
        session_id: Optional[str],
        on_token: Optional[Callable[[str], None]],
    ) -> tuple:
        """运行前共享初始化: 会话旋转、生命周期事件、token 落盘包装、trace、
        系统提示注入、hook 上下文、模型路由、用户消息构建。

        Returns:
            (on_token_wrapped, run_trace, raw_on_token)
        """
        # A4 事件溯源: 会话旋转 + 生命周期事件
        if session_id and self.session is not None and hasattr(self.session, "rotate"):
            try:
                self.session.rotate(session_id, meta={"task": (user_input or "")[:200]})
            except Exception:  # noqa: BLE001
                pass
        self._session_append("session.update", status="initialized")
        self._session_append("task.update", status="running")

        # 包装 on_token: 增量落盘
        raw_on_token = on_token
        def _wrapped_on_token(delta: str) -> None:
            if delta:
                self._session_append("agent_message_chunk", text=delta)
            if raw_on_token:
                raw_on_token(delta)
        on_token = _wrapped_on_token

        # 可观测性 trace
        run_trace = self._obs.start_trace("agent.run")
        if run_trace:
            self._tool_executor._trace_id = run_trace

        # 系统提示 + 任务上下文
        if not self.messages:
            self.messages.append({"role": "system", "content": self._system_prompt})

        hook_ctx = self._run_prompt_submit_hooks(user_input)
        self._maybe_route_model(user_input)
        self._route_session = RouteSession()

        task_ctx = build_task_context(
            task_hint=user_input,
            memory_store=self.kernel.get("memory_store"),
            skill_manager=self.kernel.get("skill_manager") if self.config.get("skills.auto_inject", True) else None,
        )
        prefix = "\n\n".join(x for x in (task_ctx, hook_ctx) if x)
        _expanded_input = self._resolve_cross_refs(user_input)
        user_msg: Dict[str, Any] = {
            "role": "user",
            "content": (prefix + "\n\n" + _expanded_input) if prefix else _expanded_input,
        }
        if self.pending_images:
            vision = getattr(self.model.capabilities, "vision", False)
            provider = getattr(self.model, "name", "openai")
            user_msg["content"] = build_user_content(
                user_msg["content"], self.pending_images, vision, provider
            )
            self.pending_images = []
        self.messages.append(user_msg)
        self._session_append("user", message=user_msg)

        return on_token, run_trace, raw_on_token

    def _teardown_run(
        self,
        answer: str,
        run_trace,
        raw_on_token: Optional[Callable[[str], None]],
        on_error: Optional[Callable[[str], None]],
        nudge: bool = True,
    ) -> str:
        """运行后共享收尾: hooks 通知、技能蒸馏、trace 关闭、会话状态落盘。"""
        if answer and not answer.startswith("[模型错误]") and not self._cancel_event.is_set():
            self._notify_hook("Stop", {"reason": "stop", "answer": answer[:1000]})
        if nudge and self._should_nudge():
            self._nudge_round(on_token=raw_on_token, on_reason=None, on_error=on_error)
        if run_trace:
            self._obs.finish_trace(run_trace)

        _failed = bool(answer) and answer.startswith("[模型错误]")
        self._session_append("task.update", status="failed" if _failed else "completed")
        self._session_append("session.update", status="done")
        return answer

    def run(
        self,
        user_input: str,
        stream: bool = True,
        on_token: Optional[Callable[[str], None]] = None,
        on_tool: Optional[Callable[[str, str], None]] = None,
        on_reason: Optional[Callable[[str], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        max_iterations: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """运行一轮完整对话。

        可插拔 Loop: 若 kernel 中注册了 LoopProvider, 委托给它执行;
        否则使用内联 ReAct 循环 (向后兼容)。

        session_id: 若提供, 本轮写入独立的 SessionStore 文件 (便于 qxt replay 逐会话回放);
        否则复用进程级共享的 session_store。
        """
        self.clear_cancel()

        # ---- 可插拔 Loop: 有 LoopProvider 时委托执行 ----
        loop = getattr(self, '_loop_provider', None)
        if loop is not None:
            return self._run_with_loop(
                loop, user_input,
                stream=stream, on_token=on_token, on_tool=on_tool,
                on_reason=on_reason, on_tool_result=on_tool_result,
                on_error=on_error, max_iterations=max_iterations,
                session_id=session_id,
            )

        # ---- 无 LoopProvider: 内联 ReAct 循环 (向后兼容) ----
        return self._run_react_inline(
            user_input,
            stream=stream, on_token=on_token, on_tool=on_tool,
            on_reason=on_reason, on_tool_result=on_tool_result,
            on_error=on_error, max_iterations=max_iterations,
            session_id=session_id,
        )

    def _run_with_loop(
        self,
        loop: "LoopProvider",
        user_input: str,
        *,
        stream: bool = True,
        on_token: Optional[Callable[[str], None]] = None,
        on_tool: Optional[Callable[[str, str], None]] = None,
        on_reason: Optional[Callable[[str], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        max_iterations: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """使用 LoopProvider 执行对话, 保留 Agent 的 session/trace 仪式。"""
        on_token, run_trace, raw_on_token = self._prepare_run(
            user_input, session_id, on_token,
        )

        # 委托给 LoopProvider
        try:
            answer = loop.run_loop(
                self, user_input,
                stream=stream, on_token=on_token, on_tool=on_tool,
                on_reason=on_reason, on_tool_result=on_tool_result,
                on_error=on_error, max_iterations=max_iterations,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            answer = f"[Loop 错误] {exc}"
            if on_error:
                on_error(str(exc))

        # Goal 模式 + 收尾
        self._handle_goal(answer, 0, max_iterations or 0, raw_on_token)
        return self._teardown_run(answer, run_trace, raw_on_token, on_error, nudge=False)

    def _run_react_inline(self, user_input: str, **kwargs) -> str:
        """内联 ReAct 循环 (无 LoopProvider 时的向后兼容路径)。"""
        stream = kwargs.get("stream", True)
        on_token = kwargs.get("on_token")
        on_tool = kwargs.get("on_tool")
        on_reason = kwargs.get("on_reason")
        on_tool_result = kwargs.get("on_tool_result")
        on_error = kwargs.get("on_error")
        max_iterations = kwargs.get("max_iterations")
        session_id = kwargs.get("session_id")

        on_token, run_trace, raw_on_token = self._prepare_run(
            user_input, session_id, on_token,
        )

        max_iter = resolve_max_iterations(self.config, max_iterations)
        iteration = 0
        answer = ""

        while iteration < max_iter:
            if self._cancel_event.is_set():
                break
            budget = float(self.config.get("router.budget_limit", 0.0) or 0.0)
            if budget > 0:
                current_cost = self._estimate_total_cost()
                if current_cost >= budget:
                    if raw_on_token:
                        on_token(f"\n[预算已用尽] 已花费 ${current_cost:.4f} >= 上限 ${budget:.2f}, 自动停止。\n")
                    self._session_append("budget.exceeded", cost=current_cost, budget=budget)
                    break
            iteration += 1
            self.turn_count += 1

            if self._auto.enabled and (self._auto.plan_execute or self._auto.escalate):
                override = self._auto.decide_override(self._route_session)
                if override:
                    keep_vision = getattr(getattr(self.model, "capabilities", None), "vision", False)
                    self._maybe_route_model(user_input, override=override, has_images=keep_vision)

            if self.context_manager.needs_compact(self.messages):
                self._notify_hook("PreCompact", {"trigger": "auto"})
            self.messages, _dropped = self.context_manager.compact_if_needed(self.messages)

            try:
                result = self._chat_with_retry(
                    self.messages,
                    tools=self._schemas(),
                    stream=stream, on_token=on_token, on_reason=on_reason,
                )
            except Exception as exc:  # noqa: BLE001
                err_msg = f"[模型错误] {exc}"
                if on_error:
                    on_error(str(exc))
                answer = err_msg
                break

            if result is None:
                continue
            usage = getattr(result, "usage", None)
            if usage:
                self._accumulate_usage(usage)

            msg_dict = self._parse_assistant_message(result)
            self.messages.append(msg_dict)
            self._session_append("assistant", message=msg_dict)
            tool_calls = msg_dict.get("tool_calls", [])
            if not tool_calls:
                answer = msg_dict.get("content", "")
                break

            n_tool_msgs_before = len(self.messages)
            retry_step(
                lambda: self._execute_tools(
                    tool_calls, on_tool=on_tool, on_tool_result=on_tool_result
                ),
                is_transient=classify_transient,
            )
            self._run_verify_loop(tool_calls, on_token=on_token)
            self._auto.observe_turn(
                self._route_session,
                tool_messages_of_turn(self.messages, n_tool_msgs_before),
            )

        # Goal 模式
        self._handle_goal(answer, iteration, max_iter, raw_on_token)

        if not answer and iteration >= max_iter:
            if self._goal:
                answer = f"(达到最大迭代次数, Goal 未完成: {self._goal})"
            else:
                answer = "(达到最大迭代次数, 已停止。)"

        return self._teardown_run(answer, run_trace, raw_on_token, on_error)

    def context_stats(self) -> Dict[str, Any]:
        """返回上下文统计: 预算、已用、压缩阈值等。"""
        budget = self.config.get("context.budget_tokens", 60000)
        estimated = estimate_messages(self.messages)
        return {
            "budget_tokens": budget,
            "estimated_tokens": estimated,
            "message_count": len(self.messages),
            "compact_trigger": self.config.get("context.compact_trigger", None),
        }

    def resilience_status(self) -> Dict[str, Any]:
        """返回韧性组件 (熔断/限流/重试) 的运行状态 —— 委托给 AgentResilience。"""
        return self._resilience.status()

    def _compress_if_needed(self) -> None:
        """压缩上下文 (如需要)。"""
        self.messages, _dropped = self.context_manager.compact_if_needed(
            self.messages
        )

    def compact(self) -> int:
        """手动压缩上下文 (对标 Claude Code /compact)。返回折叠的消息条数。"""
        # 用户级 Hooks: PreCompact (手动触发)
        self._notify_hook("PreCompact", {"trigger": "manual"})
        self.messages, dropped = self.context_manager.compact_force(self.messages)
        return dropped

    def _summarize(self, messages: List[Dict[str, Any]]) -> str:
        """从旧消息中提取结构化关键信息, 用于上下文压缩。"""
        return summarize_messages(messages)
