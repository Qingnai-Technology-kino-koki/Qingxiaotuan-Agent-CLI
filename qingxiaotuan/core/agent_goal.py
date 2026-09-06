"""Agent Goal 模式 —— 从 agent.py 提取的独立职责。

对标 Claude Code /goal: 设定完成条件, 循环直到满足。

用法:
    class Agent(GoalMixin, ...):
        pass

    agent.set_goal("all tests pass and lint is clean")
    agent.run("fix the failing tests")
    # Goal 模式会在每次 run 后检查目标是否满足
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from .agent import Agent

log = logging.getLogger(__name__)


class GoalMixin:
    """Goal 模式: 设定完成条件, 循环直到满足。

    提供 set_goal / clear_goal / _check_goal_satisfied / _handle_goal 四个方法,
    Agent 类继承后在 run 循环中调用 _handle_goal 即可。
    """

    # 这些属性由 Agent.__init__ 设置, 类型标注仅为 IDE 提示
    _goal: Optional[str]
    _goal_check_prompt: Optional[str]
    messages: List[Dict[str, Any]]
    model: Any

    def set_goal(self, goal: str, check_prompt: Optional[str] = None) -> None:
        """设置 Goal 模式: 循环执行直到条件满足。

        Args:
            goal: 目标描述 (如 'all tests pass and lint is clean')
            check_prompt: 可选的条件检查提示; 若为 None, 使用内置模板。
        """
        self._goal = goal
        if check_prompt:
            self._goal_check_prompt = check_prompt
        else:
            self._goal_check_prompt = (
                f"You are a goal checker. A coding agent is working toward this goal:\n\n"
                f"Goal: {goal}\n\n"
                f"Based on the latest agent response (shown below), determine if the goal has been achieved.\n"
                f"Reply ONLY with 'YES' if the goal is fully achieved, or 'NO' if more work is needed.\n"
                f"Be strict: all parts of the goal must be satisfied.\n\n"
                f"Latest agent response:\n"
            )
        log.info("Goal 模式已启用: %s", goal[:100])

    def clear_goal(self) -> None:
        """清除 Goal 模式。"""
        self._goal = None
        self._goal_check_prompt = None
        log.info("Goal 模式已关闭")

    def _check_goal_satisfied(self, agent_response: str) -> bool:
        """用快速模型检查目标是否已满足。

        Returns:
            True if goal is satisfied, False otherwise.
        """
        if not self._goal or not self._goal_check_prompt:
            return False

        try:
            check_messages = [
                {"role": "system", "content": "You are a precise goal checker. Reply only YES or NO."},
                {"role": "user", "content": self._goal_check_prompt + "\n" + agent_response[:2000]},
            ]

            result = self.model.chat(check_messages, tools=[], stream=False)
            if result is None:
                return False

            if hasattr(result, 'content'):
                response_text = (result.content or "").strip().upper()
            elif isinstance(result, dict):
                msg = result.get('message', result)
                response_text = str(msg.get('content', '')).strip().upper()
            else:
                response_text = str(result).strip().upper()

            return response_text.startswith('YES')

        except Exception as exc:  # noqa: BLE001
            log.debug("Goal 检查失败 (视为未满足): %s", exc)
            return False

    def _handle_goal(
        self,
        answer: str,
        iteration: int,
        max_iter: int,
        raw_on_token: Optional[Callable[[str], None]],
    ) -> None:
        """Goal 模式收尾: 检查目标是否满足, 未满足则注入继续提示。"""
        if not self._goal:
            return
        if answer.startswith("[模型错误]") or getattr(self, '_cancel_event', None) and self._cancel_event.is_set():
            return
        if self._check_goal_satisfied(answer or str(self.messages[-1])):
            if raw_on_token:
                raw_on_token(f"\n[Goal] ✓ 目标已达成: {self._goal}\n")
            self._session_append("goal.achieved", goal=self._goal, iteration=iteration)
            self._goal = None
            self._goal_check_prompt = None
        elif iteration < max_iter:
            continue_prompt = (
                f"[Goal 模式] 目标尚未达成: {self._goal}\n"
                f"请继续推进直到目标满足。当前进度需要继续。"
            )
            if raw_on_token:
                raw_on_token(f"\n[Goal] 目标未达成, 继续推进...\n")
            self.messages.append({"role": "user", "content": continue_prompt})
            self._session_append("goal.continue", goal=self._goal, iteration=iteration)
