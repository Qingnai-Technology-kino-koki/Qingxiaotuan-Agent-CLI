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
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from ..config import Config
from ..context.manager import ContextManager, estimate_messages
from .kernel import Kernel
from .prompts import build_system_prompt, build_task_context, is_short_task, system_prompt_hash
from ..tools.base import ToolContext

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
        self.yolo = config.is_yolo()
        self.plan_mode = False  # Plan Mode (对标 Claude Code: 只读模式, 不修改文件)
        self.ctx = ToolContext(kernel=kernel, workspace=workspace, confirm=confirm, yolo=self.yolo)
        self.ctx.plan_mode = self.plan_mode
        self.max_retries = config.get("agent.max_retries", 3)
        self.retry_backoff = config.get("agent.retry_backoff", 2.0)
        self.retry_jitter = config.get("agent.retry_jitter", 0.3)
        self.retry_on = set(config.get("agent.retry_on", [408, 429, 500, 502, 503, 504]))

        # 大上下文: 把代码库地图钉进系统提示 (延迟构建, 避免启动被 Ctrl+C 打断)
        codebase_map_text = ""
        if indexer is not None and config.get("context.pin_codebase", True):
            try:
                codebase_map_text = indexer.build().map_text()
            except KeyboardInterrupt:
                # 用户想取消初始化, 优雅退出而非 Traceback
                codebase_map_text = ""
            except Exception:
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
        return build_system_prompt(
            home=self.config.home,
            workspace=self.workspace,
            memory_store=self.kernel.get("memory_store"),
            skill_manager=self.kernel.get("skill_manager") if self.config.get("skills.auto_inject", True) else None,
            skill_limit=self.config.get("skills.inject_limit", 3),
            codebase_map=self._codebase_map,
        )

    # ------------------------------------------------------------ 会话事件流

    def _session_append(self, event_type: str, **payload: Any) -> None:
        """把事件写入 append-only 会话流 (可审计、可回放)。"""
        if self.session is not None:
            try:
                self.session.append(event_type, payload)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ 用量统计

    def _accumulate_usage(self, usage: Any) -> None:
        """累计模型用量 (含 DeepSeek 的 prompt cache 命中字段)。"""
        if not usage:
            return
        for key in ("prompt_tokens", "completion_tokens",
                    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            val = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            if isinstance(val, (int, float)):
                self.total_usage[key] = self.total_usage.get(key, 0) + int(val)

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
        for tc in msg_dict.get("tool_calls", []):
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", "{}")
            self._session_append("tool_call", name=fn_name, arguments=fn_args)
            if fn_name in self.exclude_tools:
                result_text = f"[已禁用] 工具 {fn_name} 已被 exclude_tools 排除"
            else:
                result_text = self.registry.dispatch(fn_name, fn_args, self.ctx)
            tool_msg = {
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": fn_name,
                "content": result_text,
            }
            self.messages.append(tool_msg)
            self._session_append("tool", message=tool_msg, name=fn_name, content=result_text)

    # ------------------------------------------------------------ 主循环

    def cancel(self) -> None:
        """请求在当前模型调用或工具步骤结束后停止当前任务。"""
        self._cancel_event.set()

    def clear_cancel(self) -> None:
        """清除取消请求，准备执行下一项任务。"""
        self._cancel_event.clear()

    def _chat_with_retry(self, messages, tools, stream, on_token, on_reason, label="模型"):
        """带超时/重试的模型调用: 失败按指数退避+抖动重试, 429 等限流按 Retry-After 退避。"""
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return self.model.chat(
                    messages, tools=tools, stream=stream,
                    on_token=on_token, on_reason=on_reason,
                )
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                kind, status = self._classify(exc)
                if kind == "auth":
                    raise RuntimeError(
                        f"{label}鉴权失败 (HTTP {status}): 请检查 API Key 是否正确、未过期、"
                        f"且对当前模型有访问权限。"
                    ) from exc
                if attempt >= self.max_retries or (status is not None and status not in self.retry_on):
                    break
                if kind == "rate_limit":
                    wait = self._retry_after(exc) or (self.retry_backoff * (2 ** (attempt - 1)))
                else:
                    wait = self.retry_backoff * (2 ** (attempt - 1))
                if self.retry_jitter:
                    wait += random.uniform(0, self.retry_jitter * wait)
                wait = min(wait, 60.0)
                self.kernel.emit("model.retry", {
                    "attempt": attempt, "wait": round(wait, 1),
                    "kind": kind, "status": status, "error": str(exc)[:160],
                })
                if on_reason and kind == "rate_limit":
                    on_reason(f"[连接] 触发限流, {wait:.0f}s 后重试 (第 {attempt}/{self.max_retries} 次)")
                time.sleep(wait)
        raise RuntimeError(f"{label}调用失败 (已重试 {self.max_retries} 次): {last_err}") from last_err

    @staticmethod
    def _classify(exc: Exception):
        try:
            from ..models.openai_compat import OpenAICompatAdapter
        except Exception:  # pragma: no cover
            return "other", None
        if hasattr(exc, "status_code") or "openai" in type(exc).__module__.lower():
            return OpenAICompatAdapter.classify_error(exc)
        return "other", None

    @staticmethod
    def _retry_after(exc: Exception) -> Optional[float]:
        resp = getattr(exc, "response", None)
        headers = getattr(resp, "headers", None)
        if headers:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra:
                try:
                    return float(ra)
                except (TypeError, ValueError):
                    pass
        return None

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

        # 构建用户输入 (附加记忆/技能上下文)
        task_ctx = build_task_context(
            task_hint=user_input,
            memory_store=self.kernel.get("memory_store"),
            skill_manager=self.kernel.get("skill_manager") if self.config.get("skills.auto_inject", True) else None,
        )
        user_msg = {"role": "user", "content": (task_ctx + "\n\n" + user_input) if task_ctx else user_input}
        self.messages.append(user_msg)
        self._session_append("user", message=user_msg)

        max_iter = max_iterations or self.config.get("agent.max_iterations", 20)
        iteration = 0
        answer = ""

        while iteration < max_iter:
            if self._cancel_event.is_set():
                break
            iteration += 1
            self.turn_count += 1

            # 压缩上下文 (长会话)
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

            # 执行工具
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"].get("arguments", "{}")
                if on_tool:
                    on_tool(fn_name, fn_args)
                self._session_append("tool_call", name=fn_name, arguments=fn_args)

                if fn_name in self.exclude_tools:
                    result_text = f"[已禁用] 工具 {fn_name} 已被 exclude_tools 排除"
                else:
                    result_text = self.registry.dispatch(fn_name, fn_args, self.ctx)

                if on_tool_result:
                    on_tool_result(fn_name, result_text)

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": fn_name,
                    "content": result_text,
                }
                self.messages.append(tool_msg)
                self._session_append("tool", message=tool_msg, name=fn_name, content=result_text)

        if not answer and iteration >= max_iter:
            answer = "(达到最大迭代次数, 已停止。)"

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
        self.messages, dropped = self.context_manager.compact_force(self.messages)
        return dropped

    def _summarize(self, messages: List[Dict[str, Any]]) -> str:
        """轻量摘要: 把旧消息折叠成一小段中文摘要。"""
        import textwrap
        parts = []
        for m in messages[-20:]:
            role = m.get("role", "")
            content = str(m.get("content", ""))[:120]
            if role and content:
                parts.append(f"{role}: {content}")
        return textwrap.fill(" ".join(parts), width=200)
