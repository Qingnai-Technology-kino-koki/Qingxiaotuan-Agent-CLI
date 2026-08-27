"""Agent 主循环 —— ReAct: think -> tool -> observe -> think ... 直到完成。

融合点:
- Harness: 循环本身也是一个挂在微内核上的组件, 每个 step 都发出内核事件
  并写入 append-only 会话事件流 (可审计、可回放)。
- Hermes: 每隔 skill_nudge_interval 个 turn, 循环会"自我提醒"反思 —
  这次的方法值不值得蒸馏成技能? 这就是自进化闭环的触发器。
- Claude Code: 大上下文机制 —— 系统提示里钉一份代码库地图, 长会话里按
  优先级智能压缩旧历史 (ContextManager), 关键信息不丢。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Set

from ..config import Config
from ..context.manager import ContextManager, estimate_messages
from .kernel import Kernel
from .retry import RetryPolicy, RateLimiter, classify_error as _classify_error, retry_after_seconds as _retry_after_seconds
from .prompts import build_system_prompt, build_task_context, is_short_task
from .tool_executor import ToolExecutor
from .auto_route import AutoRouter, RouteSession, tool_messages_of_turn
from ..tools.base import ToolContext, ToolResult
from ..vision import encode_image_source, build_user_content, build_tool_content
from ..vision.blocks import ImageRef

log = logging.getLogger(__name__)

SKILL_NUDGE = (
    "[系统提醒] 任务已推进数轮。请自查: 本次使用的方法是否具有通用性、值得在未来复用?"
    "如果是, 请用 skill_save 将其蒸馏为技能 (或改进已有的同名技能), 然后继续完成任务。"
    "如果不值得, 忽略本提醒继续即可, 不要回复本提醒。"
)


class Agent:
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
        self.ctx = ToolContext(kernel=kernel, workspace=workspace, confirm=confirm, yolo=self.yolo)
        self.ctx.plan_mode = self.plan_mode
        # 同一列表对象: checkpoint 工具经 ctx.conversation 截断对话流 (rewind)
        self.ctx.conversation = self.messages
        # 反向引用: 让工具 handler 能同步 agent 级状态 (如 enter/exit_plan_mode 同步 plan_mode)
        self.ctx.agent = self
        # 多模态: 用户挂接的图片缓冲 (一次性, 随下一轮 user 消息发送后清空)
        self.pending_images: List[ImageRef] = []
        # 自动模型路由: 失败保险 = 仅切换到「已配置密钥」的供应商; 自定义模型不路由。
        # _model_switcher 延迟绑定到 ModelPlugin.switch_model, 避免模块级导入环。
        self._model_switcher: Optional[Callable[..., Any]] = None
        self._last_route: Optional[Dict[str, Any]] = None  # 最近一次路由决策 (供 /route 与 /cost 展示)
        # 规划/执行 + 卡住升级的跨 turn 路由状态 (core/auto_route)
        self._auto = AutoRouter(self.config)
        self._route_session = RouteSession()
        # 重试策略: 独立组件 (core/retry.py), 属性代理保持旧接口兼容
        self.retry_policy = RetryPolicy.from_config(config)
        # 客户端限流: 发送前主动节流 (model.rate_limit, 默认关闭), 保护免费层端点
        self._rate_limiter = RateLimiter.from_config(config)

        # 工具执行器 (从 Agent 拆出的独立组件)
        self._tool_executor = ToolExecutor(
            registry=self.registry,
            messages=self.messages,
            session_append=self._session_append,
            tool_content_fn=self._tool_content,
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

    def _session_append(self, event_type: str, **payload: Any) -> None:
        """把事件写入 append-only 会话流 (可审计、可回放)。"""
        if self.session is not None:
            try:
                self.session.append(event_type, payload)
            except Exception as exc:  # noqa: BLE001
                log.debug("会话事件写入失败 (%s): %s", event_type, exc)

    # ------------------------------------------------------------ 用量统计

    def _accumulate_usage(self, usage: Any) -> None:
        """累计模型用量 (含 DeepSeek 的 prompt cache 命中字段), 并把本次增量落盘会话流。"""
        if not usage:
            return
        delta: Dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens",
                    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            val = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            if isinstance(val, (int, float)):
                delta[key] = int(val)
                self.total_usage[key] = self.total_usage.get(key, 0) + int(val)
        if not delta:
            return
        # 用量事件落盘 (对标 Claude Code 的 /cost 跨会话统计):
        # 每次调用记一条增量, 汇总交给 sessions.sum_usage。
        provider = self.config.get("model.provider", "")
        model = self.config.get("model.model", "")
        cost_usd: Optional[float]
        try:
            from ..models.router import estimate_cost
            cost_usd = estimate_cost(provider, model,
                                     delta.get("prompt_tokens", 0),
                                     delta.get("completion_tokens", 0))
        except Exception:  # noqa: BLE001 - 定价表缺失不影响用量记录
            cost_usd = None
        self._session_append(
            "usage",
            turn=self.turn_count,
            delta=delta,
            provider=provider,
            model=model,
            cost_usd=cost_usd,
        )

    def _estimate_total_cost(self) -> float:
        """估算当前会话总花费 (USD)。"""
        try:
            from ..models.router import estimate_cost
            provider = self.config.get("model.provider", "")
            model = self.config.get("model.model", "")
            return estimate_cost(
                provider, model,
                self.total_usage.get("prompt_tokens", 0),
                self.total_usage.get("completion_tokens", 0),
            ) or 0.0
        except Exception:  # noqa: BLE001
            return 0.0

    def cache_hit_rate(self) -> Optional[float]:
        """DeepSeek 前缀缓存命中率: hit / (hit + miss)。无数据返回 None。"""
        hit = self.total_usage.get("prompt_cache_hit_tokens", 0)
        miss = self.total_usage.get("prompt_cache_miss_tokens", 0)
        total = hit + miss
        if total <= 0:
            return None
        return hit / total

    # ------------------------------------------------------------ 技能蒸馏 (Hermes 闭环)

    @property
    def _nudge_interval(self) -> int:
        return int(self.config.get("agent.skill_nudge_interval", 3))

    def _should_nudge(self) -> bool:
        interval = self._nudge_interval
        return interval > 0 and self.turn_count > 0 and self.turn_count % interval == 0

    def _nudge_round(self, on_token=None, on_reason=None, on_error=None) -> None:
        """任务推进数轮后自我提醒: 本次方法是否值得蒸馏成技能。

        模型若决定蒸馏, 会返回 skill_save 工具调用, 这里执行之; 若回复空内容
        表示无需蒸馏, 直接结束。nudge 是单轮收尾, 不再继续循环。
        """
        self.messages.append({"role": "user", "content": SKILL_NUDGE})
        try:
            result = self._chat_with_retry(
                self.messages,
                tools=self.registry.schemas(),
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
        if hasattr(result, "content"):
            msg_dict: Dict[str, Any] = {
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

    # ------------------------------------------------------------ 多模态 (视觉)

    def attach_image(self, spec: str) -> str:
        """挂接一张图片 (本地路径 / http(s) URL / data: URI), 随下一轮 user 消息发送。

        返回面向用户的提示文案。失败抛异常由调用方捕获。
        """
        max_mb = int(self.config.get("agent.vision_max_mb", 15))
        ref = encode_image_source(spec, self.workspace, max_mb * 1024 * 1024)
        self.pending_images.append(ref)
        if ref.is_remote():
            return f"已挂接远程图片: {spec}（模型支持视觉时将在下一轮送达）"
        return f"已挂接图片: {ref.path}（{ref.byte_size() // 1024}KB, {ref.media_type}）"

    def clear_pending_images(self) -> int:
        """清空待发送的图片缓冲, 返回清除的数量。"""
        n = len(self.pending_images)
        self.pending_images = []
        return n

    def _tool_content(self, result):
        """把工具返回结果转成 tool 消息的 content。

        工具返回图片且模型支持视觉 -> [text 块, image_url 块, ...];
        否则原样 (文字或 ToolResult, 由下游 str 化)。
        """
        if isinstance(result, ToolResult) and result.images:
            vision = getattr(self.model.capabilities, "vision", False)
            if vision:
                return build_tool_content(result.content, result.images, vision)
        return result

    def _chat_with_retry(self, messages, tools, stream, on_token, on_reason, label="模型"):
        """带超时/重试的模型调用: 策略在 core/retry.RetryPolicy (指数退避+抖动, 限流按 Retry-After)。"""
        self._rate_limiter.acquire()
        try:
            return self.retry_policy.call(
                lambda: self.model.chat(
                    messages, tools=tools, stream=stream,
                    on_token=on_token, on_reason=on_reason,
                ),
                label=label,
                emit=self.kernel.emit,
                on_rate_limit_notice=on_reason,
            )
        finally:
            self._rate_limiter.release()

    # ------------------------------------------------------------ 自动模型路由

    def _maybe_route_model(self, task: str, override: int = 0, has_images: Optional[bool] = None) -> None:
        """按 router 配置自动选择/切换模型 (失败保险: 仅切换到已配置密钥的供应商)。

        仅当「升级 / 降级省钱 / 当前缺失必需能力(如视觉)」时才切换, 避免同档抖动。
        任何异常都 fail-safe: 保持当前模型继续工作, 不打断用户任务。

        Args:
            task: 本轮任务文本 (用于难度估算)。
            override: 难度覆盖值 (0=自动评估; 2=便宜; 10=强)。由 core/auto_route 计算,
                      用于实现「规划用强模型 / 执行用便宜模型 / 卡住升级」。
        """
        if not self.config.get("router.enabled", False):
            return
        if not self.config.get("router.auto_switch", True):
            return
        try:
            from ..models.router import ModelRouter
        except Exception:  # noqa: BLE001
            return

        router = ModelRouter(
            default_provider=self.config.get("model.provider", "deepseek"),
            default_model=self.config.get("model.model", "deepseek-chat"),
        )
        cfg_override = int(self.config.get("router.difficulty_override", 0) or 0)
        override = override or cfg_override
        if has_images is None:
            has_images = bool(self.pending_images)
        cur_provider = self.config.get("model.provider")
        cur_model = self.config.get("model.model")
        avail = ModelRouter.available_provider_names()
        decision = router.decide(
            task, context="", current_provider=cur_provider, current_model=cur_model,
            has_images=has_images, available_providers=avail, difficulty_override=override,
        )
        self._last_route = decision
        if not decision.get("switch"):
            return

        overrides = {
            "provider": decision["provider"],
            "model": decision["model"],
            "base_url": decision.get("base_url", ""),
            "api_key_env": decision.get("api_key_env", ""),
        }
        try:
            if self._model_switcher is None:
                from ..models.plugin import ModelPlugin
                self._model_switcher = ModelPlugin.switch_model
            self._model_switcher(self.kernel, overrides)
            # switch_model 已把新适配器重新注册进内核, 这里同步到当前实例
            self.model = self.kernel.require("model_adapter")
            self.kernel.emit("model.routed", decision)
            log.info("自动路由切换: %s/%s — %s", decision["provider"], decision["model"], decision["reason"])
        except Exception as exc:  # noqa: BLE001
            log.debug("模型自动路由切换失败, 保持当前模型: %s", exc)

    # ------------------------------------------------------------ 工具执行 (委托 ToolExecutor)

    def _execute_tools(
        self,
        tool_calls: List[Dict[str, Any]],
        on_tool: Optional[Callable[[str, str], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """执行一批工具调用 —— 委托给 ToolExecutor (core/tool_executor.py)。"""
        self._tool_executor.execute_batch(
            tool_calls, self.ctx,
            exclude_tools=self.exclude_tools,
            on_tool=on_tool,
            on_tool_result=on_tool_result,
        )

    # ---- 重试策略属性的兼容代理 (外部测试/调用方仍可读写 agent.max_retries 等) ----

    @property
    def max_retries(self) -> int:
        return self.retry_policy.max_retries

    @max_retries.setter
    def max_retries(self, v) -> None:
        self.retry_policy.max_retries = int(v)

    @property
    def retry_backoff(self) -> float:
        return self.retry_policy.backoff

    @retry_backoff.setter
    def retry_backoff(self, v) -> None:
        self.retry_policy.backoff = float(v)

    @property
    def retry_jitter(self) -> float:
        return self.retry_policy.jitter

    @retry_jitter.setter
    def retry_jitter(self, v) -> None:
        self.retry_policy.jitter = float(v)

    @property
    def retry_on(self) -> set:
        return self.retry_policy.retry_on

    @retry_on.setter
    def retry_on(self, v) -> None:
        self.retry_policy.retry_on = set(v)

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
    ) -> str:
        """运行一轮完整对话。"""
        self.clear_cancel()
        if not self.messages:
            # 首次对话: 生成系统提示 + 任务上下文
            self.messages.append({"role": "system", "content": self._system_prompt})

        # 用户级 Hooks: UserPromptSubmit (stdout 作为附加上下文注入本回合)
        hook_ctx = self._run_prompt_submit_hooks(user_input)

        # 自动模型路由: 按本回合任务难度/必需能力选择模型 (失败保险: 仅切到已配密钥的供应商)。
        # 必须在构建 user 消息之前执行, 这样切换后的视觉能力才能正确计入挂图。
        self._maybe_route_model(user_input)
        # 规划/执行 + 卡住升级: 每个 run() 重置跨 turn 路由状态 (一次用户输入 = 一个任务)。
        self._route_session = RouteSession()

        # 构建用户输入 (附加记忆/技能上下文)
        task_ctx = build_task_context(
            task_hint=user_input,
            memory_store=self.kernel.get("memory_store"),
            skill_manager=self.kernel.get("skill_manager") if self.config.get("skills.auto_inject", True) else None,
        )
        prefix = "\n\n".join(x for x in (task_ctx, hook_ctx) if x)
        user_msg: Dict[str, Any] = {"role": "user", "content": (prefix + "\n\n" + user_input) if prefix else user_input}
        # 多模态: 若本回合挂接了图片, 且模型支持视觉, 则把 content 升级为
        # [text 块, image_url 块, ...]; 不支持视觉的模型降级为路径注记 (不发送字节)。
        if self.pending_images:
            vision = getattr(self.model.capabilities, "vision", False)
            provider = getattr(self.model, "name", "openai")
            user_msg["content"] = build_user_content(
                user_msg["content"], self.pending_images, vision, provider
            )
            self.pending_images = []  # 一次性挂接, 发送后清空
        self.messages.append(user_msg)
        self._session_append("user", message=user_msg)

        max_iter = max_iterations or self.config.get("agent.max_iterations", 20)
        iteration = 0
        answer = ""

        while iteration < max_iter:
            if self._cancel_event.is_set():
                break
            # 硬预算控制 (对标 Claude Code --max-cost): 超过上限自动停止
            budget = float(self.config.get("router.budget_limit", 0.0) or 0.0)
            if budget > 0:
                current_cost = self._estimate_total_cost()
                if current_cost >= budget:
                    if on_token:
                        on_token(f"\n[预算已用尽] 已花费 ${current_cost:.4f} >= 上限 ${budget:.2f}, 自动停止。\n")
                    self._session_append("budget.exceeded", cost=current_cost, budget=budget)
                    break
            iteration += 1
            self.turn_count += 1

            # 规划/执行 + 卡住升级: 每轮按路由策略决定难度覆盖 (2=便宜 / 10=强),
            # 失败保险在 _maybe_route_model → router.decide 内 (绝不切到无凭证端点)。
            # 仅当策略要求「强制」时才介入 —— override 为 0 (无规划/未卡住) 时不动,
            # 以免覆盖 run() 开头为看图任务做出的视觉模型切换。强制时保留当前模型的
            # 视觉能力 (has_images 取当前模型 capabilities.vision), 避免降级掉看图能力。
            if self._auto.enabled and (self._auto.plan_execute or self._auto.escalate):
                override = self._auto.decide_override(self._route_session)
                if override:
                    keep_vision = getattr(getattr(self.model, "capabilities", None), "vision", False)
                    self._maybe_route_model(
                        user_input, override=override, has_images=keep_vision
                    )

            # 压缩上下文 (长会话); 确认即将压缩时先发 PreCompact hook
            if self.context_manager.needs_compact(self.messages):
                self._notify_hook("PreCompact", {"trigger": "auto"})
            self.messages, _dropped = self.context_manager.compact_if_needed(
                self.messages
            )

            # 调用模型
            try:
                result = self._chat_with_retry(
                    self.messages,
                    tools=self.registry.schemas(),
                    stream=stream,
                    on_token=on_token,
                    on_reason=on_reason,
                )
            except Exception as exc:  # noqa: BLE001
                err_msg = f"[模型错误] {exc}"
                if on_error:
                    on_error(str(exc))
                answer = err_msg
                break

            if result is None:
                continue

            # 累计用量 (含缓存命中字段)
            usage = getattr(result, "usage", None)
            if usage:
                self._accumulate_usage(usage)

            # 处理结果: ModelResponse 数据类 或 dict
            if hasattr(result, "content"):
                # ModelResponse 数据类
                msg_dict: Dict[str, Any] = {
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

            self.messages.append(msg_dict)
            self._session_append("assistant", message=msg_dict)

            tool_calls = msg_dict.get("tool_calls", [])
            if not tool_calls:
                answer = msg_dict.get("content", "")
                break

            # 执行工具 (只读工具并行, 写工具串行)
            n_tool_msgs_before = len(self.messages)
            self._execute_tools(
                tool_calls, on_tool=on_tool, on_tool_result=on_tool_result,
            )
            # 卡住检测: 用本轮工具结果更新路由状态 (连续失败 → 下一轮升级强模型救场)。
            self._auto.observe_turn(
                self._route_session,
                tool_messages_of_turn(self.messages, n_tool_msgs_before),
            )

        if not answer and iteration >= max_iter:
            answer = "(达到最大迭代次数, 已停止。)"

        # 用户级 Hooks: Stop (主回合自然结束; 模型错误或用户取消时不触发)
        if (answer
                and not answer.startswith("[模型错误]")
                and not self._cancel_event.is_set()):
            self._notify_hook("Stop", {"reason": "stop", "answer": answer[:1000]})

        # 技能蒸馏 (Hermes 闭环): 任务推进数轮后自我提醒一次
        if self._should_nudge():
            self._nudge_round(on_token=on_token, on_reason=on_reason, on_error=on_error)

        return answer

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
        """从旧消息中提取结构化关键信息, 用于上下文压缩。

        规则提取 (不依赖 LLM, 避免递归调用):
        - 用户指令/目标 (user 消息中的核心意图)
        - 工具调用中涉及的文件路径
        - 工具调用的执行结果摘要 (成功/失败)
        - assistant 回复中的关键结论
        """
        goals: list[str] = []
        files_seen: set[str] = set()
        tool_actions: list[str] = []
        conclusions: list[str] = []

        for m in messages:
            role = m.get("role", "")
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(str(p) for p in content)
            content = str(content)

            if role == "user" and content.strip():
                # 提取用户意图: 取前 120 字符作为目标摘要
                line = content.strip().split("\n")[0][:120]
                if line and line not in goals:
                    goals.append(line)

            elif role == "assistant":
                # 提取工具调用中的文件路径
                for tc in m.get("tool_calls", []) or []:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args_str = fn.get("arguments", "")
                    # 文件操作工具: 提取文件名
                    if name in ("read_file", "write_file", "edit_file", "str_replace", "open_file"):
                        try:
                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                            path = args.get("path", "")
                            if path and path not in files_seen:
                                files_seen.add(path)
                                tool_actions.append(f"{name}({path})")
                        except (json.JSONDecodeError, AttributeError):
                            pass
                    elif name == "run_terminal_command":
                        try:
                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                            cmd = args.get("command", "")[:80]
                            if cmd:
                                tool_actions.append(f"$ {cmd}")
                        except (json.JSONDecodeError, AttributeError):
                            pass
                    elif name == "code_search":
                        try:
                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                            pattern = args.get("pattern", "")[:60]
                            if pattern:
                                tool_actions.append(f"search({pattern})")
                        except (json.JSONDecodeError, AttributeError):
                            pass
                # assistant 回复中的关键结论 (取最后 200 字)
                if content.strip():
                    tail = content.strip()[-200:]
                    if tail and tail not in conclusions:
                        conclusions.append(tail)

        # 组装结构化摘要
        parts: list[str] = []
        if goals:
            parts.append("用户目标: " + "; ".join(goals[-3:]))  # 最多保留最近 3 个
        if files_seen:
            parts.append("涉及文件: " + ", ".join(sorted(files_seen)[:8]))  # 最多 8 个
        if tool_actions:
            parts.append("操作: " + "; ".join(tool_actions[-6:]))  # 最多保留最近 6 个
        if conclusions:
            parts.append("结论: " + conclusions[-1][:200])  # 取最后一条结论
        if not parts:
            # fallback: 无结构化信息可提取, 返回原始内容摘要
            flat = " ".join(
                f"{m.get('role', '?')}: {str(m.get('content', ''))[:80]}"
                for m in messages[-10:] if m.get("content")
            )
            return flat[:400] if flat else "(无可提取的结构化信息)"
        return "\n".join(parts)[:500]  # 总长限制 500 字符
