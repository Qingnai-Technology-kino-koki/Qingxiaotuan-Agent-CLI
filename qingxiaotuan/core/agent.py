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
        self.exclude_tools: Set[str] = set(exclude_tools)
        self.yolo = config.is_yolo()
        self.plan_mode = False  # Plan Mode (对标 Claude Code: 只读模式, 不修改文件)
        self.ctx = ToolContext(kernel=kernel, workspace=workspace, confirm=confirm, yolo=self.yolo)
        self.max_retries = config.get("agent.max_retries", 3)
        self.retry_backoff = config.get("agent.retry_backoff", 2.0)
        self.retry_jitter = config.get("agent.retry_jitter", 0.3)
        self.retry_on = set(config.get("agent.retry_on", [408, 429, 500, 502, 503, 504]))

        # 大上下文: 把代码库地图钉进系统提示
        codebase_map_text = ""
        if indexer is not None and config.get("context.pin_codebase", True):
            try:
                codebase_map_text = indexer.build().map_text()
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
        """组装「绝对稳定」的系统提示 (不含随任务变化的语义召回)。

        为最大化 prompt cache 命中, system 前缀在同一工作区内逐字节不变:
        语义召回 (记忆/技能) 由 build_task_context 生成, 贴在首条 user 消息前,
        而非塞进 system —— 见 run() 的首轮处理。
        """
        return build_system_prompt(
            home=self.config.home,
            workspace=self.workspace,
            memory_store=self.kernel.get("memory_store"),
            skill_manager=self.kernel.get("skill_manager") if self.config.get("skills.auto_inject", True) else None,
            skill_limit=self.config.get("skills.inject_limit", 3),
            codebase_map=self._codebase_map,
        )

    # ------------------------------------------------------------ 主循环

    def _chat_with_retry(self, messages, tools, stream, on_token, on_reason, label="模型"):
        """带超时/重试的模型调用: 失败按指数退避+抖动重试, 429 等限流按 Retry-After 退避。

        策略:
        - 仅对可重试错误 (429/5xx/超时, 见 agent.retry_on) 重试;
        - 指数退避 backoff * 2^(n-1) 叠加 ±jitter 抖动, 避免惊群;
        - 遇到 429 且服务端给出 Retry-After, 优先服从该值;
        - auth 类错误 (401/403) 立即失败, 不浪费重试;
        - 超过 max_retries 才抛出, 经 on_error 回传 UI。
        """
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
                # 不可重试的鉴权/客户端错误: 直接失败
                if kind == "auth":
                    raise RuntimeError(
                        f"{label}鉴权失败 (HTTP {status}): 请检查 API Key 是否正确、未过期、"
                        "且对当前模型有访问权限。"
                    ) from exc
                # 超过重试次数或不在重试清单: 放弃
                if attempt >= self.max_retries or (status is not None and status not in self.retry_on):
                    break
                # 计算等待时长
                if kind == "rate_limit":
                    wait = self._retry_after(exc) or (self.retry_backoff * (2 ** (attempt - 1)))
                else:
                    wait = self.retry_backoff * (2 ** (attempt - 1))
                # 叠加抖动
                if self.retry_jitter:
                    wait += random.uniform(0, self.retry_jitter * wait)
                wait = min(wait, 60.0)  # 单次等待封顶 60s, 防止卡死
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
        """尝试从异常里抽取 Retry-After (秒)。"""
        # openai 异常可能携带 response headers
        resp = getattr(exc, "response", None)
        headers = getattr(resp, "headers", None)
        if headers:
            import email.utils  # noqa: F401 - 仅用于存在性探测, 实际读 header
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
    ) -> str:
        """执行一轮完整对话 (可能包含多次工具调用), 返回最终文本。"""
        max_iter = self.config.get("agent.max_iterations", 30)
        nudge_interval = self.config.get("agent.skill_nudge_interval", 3)
        show_reasoning = self.config.get("agent.show_reasoning", True)

        # 初始化系统提示 (仅首轮)
        self._ensure_system_prompt()

        # 注入任务上下文 (仅首轮)
        user_input = self._inject_task_context(user_input)

        # 记录用户消息
        self.messages.append({"role": "user", "content": user_input})
        self._record("user", {"message": self.messages[-1]})
        self.kernel.emit("turn.start", {"input": user_input[:200]})

        final_text = ""
        for step in range(max_iter):
            self._compress_if_needed()

            try:
                response = self._chat_with_retry(
                    self.messages,
                    tools=self._active_schemas() or None,
                    stream=stream,
                    on_token=on_token,
                    on_reason=on_reason if show_reasoning else None,
                )
            except RuntimeError as exc:
                msg = f"[模型错误] {exc}"
                if on_error:
                    on_error(msg)
                self.kernel.emit("turn.error", {"error": msg})
                return msg

            self._accumulate_usage(response.usage)
            self.kernel.emit("turn.step", {
                "step": step,
                "tool_calls": len(response.tool_calls),
                "finish": response.finish_reason,
            })

            if response.tool_calls:
                # 处理工具调用
                self._process_tool_calls(
                    response, on_tool=on_tool, on_tool_result=on_tool_result,
                )
                continue  # 工具结果回喂, 进入下一轮思考

            # 无工具调用 = 任务完成
            final_text = response.content
            self.messages.append({"role": "assistant", "content": final_text})
            self._record("assistant", {"message": {"role": "assistant", "content": final_text}})
            self.turn_count += 1

            # Hermes 技能反思 nudge
            self._maybe_nudge_skill(on_tool, on_tool_result, on_reason, show_reasoning)

            break
        else:
            final_text = final_text or "[已达最大迭代次数, 任务可能未完成]"

        self.kernel.emit("turn.end", {"turns": self.turn_count})
        return final_text

    # ------------------------------------------------------------ 主循环辅助

    def _ensure_system_prompt(self) -> None:
        """确保系统提示已初始化 (仅首轮构建, 后续复用)。"""
        if not self.messages:
            self._system_prompt = self._build_system()
            self.messages.append({"role": "system", "content": self._system_prompt})
            self._system_hash = system_prompt_hash(self._system_prompt)
        self.kernel.emit("system.stable", {"hash": getattr(self, "_system_hash", "")})

    def _inject_task_context(self, user_input: str) -> str:
        """为首条 user 消息注入任务上下文 (语义召回记忆/技能), 不污染 system 缓存前缀。"""
        first_user = not any(m.get("role") == "user" for m in self.messages)
        if not first_user:
            return user_input
        ctx_block = build_task_context(
            user_input,
            memory_store=self.kernel.get("memory_store"),
            skill_manager=self.kernel.get("skill_manager") if self.config.get("skills.auto_inject", True) else None,
            skill_limit=self.config.get("skills.inject_limit", 3),
        )
        if is_short_task(user_input, int(self.config.get("context.short_task_max_chars", 160))):
            ctx_block = ""
        else:
            limit = int(self.config.get("context.max_task_context_chars", 1800))
            ctx_block = ctx_block[:limit]
        if ctx_block:
            user_input = f"{ctx_block}\n\n[用户任务]\n{user_input}"
        return user_input

    def _process_tool_calls(
        self,
        response: Any,
        on_tool: Optional[Callable[[str, str], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """处理模型返回的工具调用, 把结果回喂消息列表。"""
        assistant_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ],
        }
        self.messages.append(assistant_msg)
        self._record("assistant", {"message": {"role": "assistant", "content": response.content or ""}})

        for tc in response.tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)
            self._record("tool_call", {"name": tc.name, "arguments": tc.arguments})
            result = self.registry.dispatch(tc.name, tc.arguments, self.ctx)
            if on_tool_result:
                on_tool_result(tc.name, result)
            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result}
            self.messages.append(tool_msg)
            self._record("tool", {"message": {"role": "tool", "name": tc.name, "content": result[:2000]}})

    def _maybe_nudge_skill(
        self,
        on_tool: Optional[Callable[[str, str], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_reason: Optional[Callable[[str], None]] = None,
        show_reasoning: bool = True,
    ) -> None:
        """Hermes 技能反思 nudge: 每 N 轮提醒 Agent 蒸馏可复用技能。"""
        nudge_interval = self.config.get("agent.skill_nudge_interval", 3)
        if nudge_interval <= 0 or self.turn_count % nudge_interval != 0:
            return
        self.messages.append({"role": "user", "content": SKILL_NUDGE})
        self._record("system_nudge", {"content": "skill_nudge"})
        nudge_resp = self._chat_with_retry(
            self.messages, tools=self._active_schemas() or None, stream=False,
            on_token=None, on_reason=on_reason if show_reasoning else None,
        )
        self._accumulate_usage(nudge_resp.usage)
        if nudge_resp.tool_calls:
            self.messages.append({
                "role": "assistant",
                "content": nudge_resp.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in nudge_resp.tool_calls
                ],
            })
            for tc in nudge_resp.tool_calls:
                if on_tool:
                    on_tool(tc.name, tc.arguments)
                result = self.registry.dispatch(tc.name, tc.arguments, self.ctx)
                if on_tool_result:
                    on_tool_result(tc.name, result)
                self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                self._record("tool", {"message": {"role": "tool", "name": tc.name, "content": result[:2000]}})
        elif nudge_resp.content:
            self._record("nudge_reply", {"content": nudge_resp.content[:500]})

    # ------------------------------------------------------------ 上下文压缩

    def _compress_if_needed(self) -> None:
        if self.context_manager is None:
            self._compress_naive()
            return
        new_msgs, dropped = self.context_manager.compact_if_needed(self.messages)
        if dropped:
            self.messages = new_msgs
            self.kernel.emit("context.compressed", {"dropped": dropped})

    def _compress_naive(self) -> None:
        """无优先级管理器时的朴素折叠 (保留 system + 最近一半)。"""
        limit = self.config.get("agent.context_max_messages", 60)
        if len(self.messages) <= limit:
            return
        system = self.messages[:1]
        recent = self.messages[-(limit // 2):]
        dropped = len(self.messages) - len(system) - len(recent)
        summary = {
            "role": "user",
            "content": f"[上下文压缩] 为节省空间, 已折叠中间的 {dropped} 条历史消息。"
                       "如需回顾细节, 可用 memory_search 检索长期记忆。",
        }
        self.messages = system + [summary] + recent
        self.kernel.emit("context.compressed", {"dropped": dropped})

    def _summarize(self, text: str) -> str:
        """用模型把一段历史压缩成摘要 (供 ContextManager 使用)。"""
        try:
            resp = self.model.chat(
                [
                    {"role": "system", "content": "你是上下文压缩器, 只输出中文摘要, 不要寒暄。"},
                    {"role": "user", "content": text},
                ],
                tools=None, stream=False,
            )
            return (resp.content or "").strip()
        except Exception:
            return ""

    # ------------------------------------------------------------ 工具

    def _active_schemas(self) -> List[Dict[str, Any]]:
        if not self.exclude_tools:
            return self.registry.schemas()
        return [s for s in self.registry.schemas() if s["function"]["name"] not in self.exclude_tools]

    def context_stats(self) -> Dict[str, Any]:
        return {
            "messages": len(self.messages),
            "estimated_tokens": estimate_messages(self.messages),
            "keep_recent": self.context_manager.keep_recent if self.context_manager else 0,
            "budget_tokens": self.context_manager.budget_tokens if self.context_manager else 0,
        }

    # ------------------------------------------------------------ 内部

    def _record(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.session:
            self.session.append(event_type, payload)

    def _accumulate_usage(self, usage: Dict[str, int]) -> None:
        for key in ("prompt_tokens", "completion_tokens"):
            self.total_usage[key] += usage.get(key, 0)
        # 缓存命中累计 (DeepSeek / OpenCode Zen 返回)
        for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cache_write_token"):
            if key in usage:
                self.total_usage[key] = self.total_usage.get(key, 0) + usage[key]

    def cache_hit_rate(self) -> Optional[float]:
        """返回累计缓存命中率 (0~1); 无缓存字段时返回 None。"""
        hit = self.total_usage.get("prompt_cache_hit_tokens", 0)
        miss = self.total_usage.get("prompt_cache_miss_tokens", 0)
        total = hit + miss
        if total == 0:
            return None
        return hit / total
