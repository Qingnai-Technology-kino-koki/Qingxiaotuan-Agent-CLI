"""可插拔 Loop 架构 —— Agent 主循环的策略模式。

设计原则:
  1. Kernel 默认装载 ReActLoop, 但用户/插件可替换为任何 LoopProvider
  2. 替换方式: kernel.provide("loop_provider", MyLoop()) 或 config loop.provider
  3. LoopProvider 是纯策略: 它接收 Agent 引用 + 配置, 返回字符串结果
  4. Agent.run() 委托给当前 LoopProvider, 保持 Agent 本体精简

三种内置实现:
  - ReActLoop: 默认 think→tool→observe 循环 (从 agent.py 抽出)
  - PlannerExecuteLoop: 先用强模型规划, 再用便宜模型逐步执行
  - DevLoopProvider: 包装现有 DevLoop, 增加自主迭代能力
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Agent

log = logging.getLogger(__name__)


# ================================================================ LoopProvider 基类

class LoopProvider(ABC):
    """Loop 策略基类。

    子类实现 run_loop(), Agent 调用 current_loop.run_loop() 驱动主循环。
    """

    name: str = "base"
    description: str = ""

    @abstractmethod
    def run_loop(
        self,
        agent: "Agent",
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
        """执行一轮完整对话, 返回最终答案文本。

        Args:
            agent: Agent 实例 (提供 model/messages/registry/ctx/session 等)
            user_input: 用户本轮输入
            stream: 是否流式
            on_token/tool/reason/tool_result/error: 回调
            max_iterations: 最大迭代次数 (None=使用 config)
            session_id: 会话 ID (供 A4 事件溯源)
        """
        ...

    def should_nudge(self, agent: "Agent") -> bool:
        """是否应该触发技能蒸馏提醒。可在子类中自定义策略。"""
        from .agent_helpers import should_nudge
        interval = int(agent.config.get("agent.skill_nudge_interval", 3))
        return should_nudge(agent.turn_count, interval)

    def on_task_start(self, agent: "Agent", user_input: str) -> None:
        """任务开始钩子。子类可覆写。"""
        pass

    def on_task_end(self, agent: "Agent", answer: str, iteration: int) -> None:
        """任务结束钩子。子类可覆写。"""
        pass

    @staticmethod
    def _tool_messages_of_turn(messages: list, after_index: int) -> list:
        """取出某轮工具执行后新增的消息 (用于观察是否卡住)。"""
        return messages[after_index:] if 0 <= after_index < len(messages) else []

    @staticmethod
    def _maybe_override_model(agent: "Agent", user_input: str) -> None:
        """自动路由覆盖: plan_execute / 卡住升级时由 AutoRouter 决定是否切换模型。

        与 ReAct 主循环保持一致: 仅当 override 非 0 时才真正切换模型,
        避免每步都做昂贵的难度分类。
        """
        auto = agent._auto
        if not auto.enabled or not (auto.plan_execute or auto.escalate):
            return
        override = auto.decide_override(agent._route_session)
        if override:
            keep_vision = getattr(getattr(agent.model, "capabilities", None), "vision", False)
            agent._maybe_route_model(user_input, override=override, has_images=keep_vision)


# ================================================================ 默认: ReAct Loop

class ReActLoop(LoopProvider):
    """默认 ReAct 循环: think→tool→observe→think... 直到完成。

    这是从 agent.py 的 run() 方法抽出的标准实现。
    """

    name = "react"
    description = "标准 ReAct 循环: think → tool → observe → think"

    def run_loop(
        self,
        agent: "Agent",
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
        self.on_task_start(agent, user_input)

        max_iter = max_iterations or agent.config.get("agent.max_iterations", 20)
        answer = ""

        for iteration in range(1, max_iter + 1):
            if agent._cancel_event.is_set():
                break

            # 预算控制
            budget = float(agent.config.get("router.budget_limit", 0.0) or 0.0)
            if budget > 0:
                current_cost = agent._estimate_total_cost()
                if current_cost >= budget:
                    if on_token:
                        on_token(f"\n[预算已用尽] 已花费 ${current_cost:.4f} >= 上限 ${budget:.2f}, 自动停止。\n")
                    agent._session_append("budget.exceeded", cost=current_cost, budget=budget)
                    break

            agent.turn_count += 1

            # 自动模型路由
            self._maybe_override_model(agent, user_input)

            # 上下文压缩
            if agent.context_manager.needs_compact(agent.messages):
                agent._notify_hook("PreCompact", {"trigger": "auto"})
            agent.messages, _ = agent.context_manager.compact_if_needed(agent.messages)

            # 调用模型
            try:
                result = agent._chat_with_retry(
                    agent.messages, tools=agent._schemas(),
                    stream=stream, on_token=on_token, on_reason=on_reason,
                )
            except Exception as exc:
                err_msg = f"[模型错误] {exc}"
                if on_error:
                    on_error(str(exc))
                answer = err_msg
                break

            if result is None:
                continue

            usage = getattr(result, "usage", None)
            if usage:
                agent._accumulate_usage(usage)

            # 解析结果 (使用 Agent 的统一解析方法)
            msg_dict = agent._parse_assistant_message(result)
            agent.messages.append(msg_dict)
            agent._session_append("assistant", message=msg_dict)

            tool_calls = msg_dict.get("tool_calls", [])
            if not tool_calls:
                answer = msg_dict.get("content", "")
                break

            # 执行工具
            n_before = len(agent.messages)
            agent._execute_tools(tool_calls, on_tool=on_tool, on_tool_result=on_tool_result)
            agent._run_verify_loop(tool_calls, on_token=on_token)
            agent._auto.observe_turn(
                agent._route_session,
                self._tool_messages_of_turn(agent.messages, n_before),
            )

        # 达到最大迭代
        if not answer and iteration >= max_iter:
            answer = "(达到最大迭代次数, 已停止。)"

        # 技能蒸馏
        if self.should_nudge(agent):
            agent._nudge_round(on_token=on_token, on_reason=on_reason, on_error=on_error)

        self.on_task_end(agent, answer, iteration)
        return answer


# ================================================================ PlannerExecute Loop

class PlannerExecuteLoop(LoopProvider):
    """先规划后执行: 用强模型拆解任务为步骤, 再逐步用便宜模型执行。

    比纯 ReAct 省 token (规划只做一次), 适合复杂多步任务。
    """

    name = "plan_execute"
    description = "先规划后执行: 强模型规划 → 便宜模型逐步执行"

    def run_loop(
        self,
        agent: "Agent",
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
        self.on_task_start(agent, user_input)

        # Phase 1: 规划 (用强模型)
        plan = self._plan(agent, user_input, on_token=on_token)
        if on_token:
            on_token(f"\n📋 规划:\n{plan}\n\n")

        # Phase 2: 逐步执行 (用便宜模型)
        max_iter = max_iterations or agent.config.get("agent.max_iterations", 20)
        steps = self._parse_plan(plan)
        answer = ""

        for i, step in enumerate(steps):
            if agent._cancel_event.is_set():
                break
            if i >= max_iter:
                break

            agent.turn_count += 1

            # 自动模型路由: 与 ReAct 主循环保持一致
            self._maybe_override_model(agent, user_input)

            # 注入当前步骤
            step_msg = f"[步骤 {i+1}/{len(steps)}] {step}\n请执行此步骤, 完成后汇报结果。"
            agent.messages.append({"role": "user", "content": step_msg})
            agent._session_append("user", message={"role": "user", "content": step_msg})

            # 压缩
            if agent.context_manager.needs_compact(agent.messages):
                agent.messages, _ = agent.context_manager.compact_if_needed(agent.messages)

            # 执行
            try:
                result = agent._chat_with_retry(
                    agent.messages, tools=agent._schemas(),
                    stream=stream, on_token=on_token, on_reason=on_reason,
                )
            except Exception as exc:
                if on_error:
                    on_error(str(exc))
                answer = f"[步骤 {i+1} 失败] {exc}"
                break

            if result is None:
                continue

            usage = getattr(result, "usage", None)
            if usage:
                agent._accumulate_usage(usage)

            msg_dict = agent._parse_assistant_message(result)
            agent.messages.append(msg_dict)
            agent._session_append("assistant", message=msg_dict)

            tool_calls = msg_dict.get("tool_calls", [])
            if tool_calls:
                n_before = len(agent.messages)
                agent._execute_tools(tool_calls, on_tool=on_tool, on_tool_result=on_tool_result)
                agent._run_verify_loop(tool_calls, on_token=on_token)
                agent._auto.observe_turn(
                    agent._route_session,
                    self._tool_messages_of_turn(agent.messages, n_before),
                )

            answer = msg_dict.get("content", "")

        self.on_task_end(agent, answer, len(steps))
        return answer

    def _plan(self, agent: "Agent", task: str, on_token=None) -> str:
        """用强模型规划任务步骤。"""
        plan_prompt = (
            f"请把下面的任务拆解成清晰的执行步骤 (3-8 步), 每步一行, 以数字编号。\n"
            f"只输出步骤列表, 不要解释。\n\n任务: {task}"
        )
        try:
            # 尝试用 worker 模型规划 (如果配置了)
            plan_result = agent._chat_with_retry(
                [{"role": "user", "content": plan_prompt}],
                tools=[], stream=False, on_token=None, on_reason=None,
            )
            if hasattr(plan_result, "content"):
                return plan_result.content or task
            elif isinstance(plan_result, dict):
                return plan_result.get("content", task) if isinstance(plan_result.get("content"), str) else task
            return str(plan_result) if plan_result else task
        except Exception:
            return task  # 规划失败, 把原任务当单步执行

    @staticmethod
    def _parse_plan(plan: str) -> List[str]:
        """从规划文本中解析步骤列表。"""
        import re
        steps = []
        for line in plan.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # 匹配 "1. xxx" / "1) xxx" / "步骤1: xxx"
            m = re.match(r"^\d+[\.\)]\s*(.+)$", line)
            if m:
                steps.append(m.group(1).strip())
                continue
            m = re.match(r"^步骤\s*\d+[：:]\s*(.+)$", line)
            if m:
                steps.append(m.group(1).strip())
                continue
            # 非编号行: 如果已有步骤, 这是补充说明
            if steps and not line[0].isdigit():
                steps[-1] += " " + line
        return steps if steps else [plan.strip()]


# ================================================================ DevLoop Provider

class DevLoopProvider(LoopProvider):
    """包装现有 DevLoop, 提供自主迭代能力。

    DevLoop 在 ReAct 基础上增加: 反思、降级、用户检查点、收敛检测。
    """

    name = "devloop"
    description = "自主开发循环: ReAct + 反思 + 降级 + 用户检查点"

    def run_loop(
        self,
        agent: "Agent",
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
        from .devloop import DevLoop

        self.on_task_start(agent, user_input)

        def _on_checkpoint(report: str) -> str:
            """检查点: 在 TUI 中展示并获取用户反馈。"""
            if on_token:
                on_token(f"\n--- 检查点 ---\n{report[:500]}\n---\n")
            return ""  # headless: 继续循环

        loop = DevLoop(
            agent=agent,
            config=agent.config,
            on_checkpoint=_on_checkpoint,
        )
        answer = loop.run(
            task=user_input,
            stream=stream,
            on_token=on_token,
            on_tool=on_tool,
            on_reason=on_reason,
        )

        self.on_task_end(agent, answer, agent.turn_count)
        return answer


# ================================================================ Loop 注册表

class LoopRegistry:
    """Loop 实例注册表: 管理可用的 LoopProvider 并按名称切换。

    Kernel 默认注册 ReActLoop; 用户可通过 config loop.provider 切换。
    """

    def __init__(self) -> None:
        self._loops: Dict[str, LoopProvider] = {}
        self._current: Optional[LoopProvider] = None

    def register(self, loop: LoopProvider) -> None:
        self._loops[loop.name] = loop
        if self._current is None:
            self._current = loop

    def set_current(self, name: str) -> None:
        if name in self._loops:
            self._current = self._loops[name]
        else:
            raise ValueError(f"未知 Loop: {name}. 可用: {list(self._loops.keys())}")

    def get_current(self) -> LoopProvider:
        return self._current or self._loops.get("react", ReActLoop())

    def list_available(self) -> List[Dict[str, str]]:
        return [{"name": l.name, "description": l.description} for l in self._loops.values()]

    @property
    def current_name(self) -> str:
        return self._current.name if self._current else "react"
