"""自动模型路由的「规划/执行 + 卡住升级」策略。

设计目标 (面向「多模型经济 + 编程能力」超越 Claude Code 的一条主线):
- 规划阶段用强模型 (tier 3): 把任务拆成清晰、可执行的步骤, 少走弯路;
- 执行阶段用便宜模型 (tier 1/2): 真正改代码、跑命令, 省钱提速;
- 便宜模型连续失败 / 卡在同一类错误 → 自动升级到强模型救场, 救完再降回便宜。

全部 fail-safe: 无可用强模型时保持当前, 绝不切到无凭证端点 (见 models/router.decide)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

log = logging.getLogger("qingxiaotuan.auto_route")


@dataclass
class RouteSession:
    """一次 run() 内跨 turn 的路由状态。"""

    turn: int = 0                       # 已完成多少轮工具迭代 (从 0 递增)
    stuck: int = 0                      # 连续失败轮数
    escalated_until: int = -1           # 救场窗口覆盖到的 turn (>= 当前则仍用强模型)


def _is_tool_failed(msg: Dict[str, Any]) -> bool:
    """判断一条 tool 消息是否代表执行失败 (供卡住升级使用)。

    依次看: 消息顶层 status (core/tool_executor 写入) → content 字典的 status →
    文本错误前缀 (项目约定 '[错误]/[拒绝]/[超时]')。成功/缓存命中不算失败。
    """
    status = msg.get("status")
    if isinstance(status, str):
        return status not in ("ok", "cached")
    content = msg.get("content")
    if isinstance(content, dict):
        st = content.get("status")
        if isinstance(st, str):
            return st not in ("ok", "cached")
    if isinstance(content, str):
        return content.startswith(("[错误]", "[拒绝", "[超时]", "[denied]", "[timeout]"))
    return False


class AutoRouter:
    """把「强规划 / 便宜执行 / 卡住升级」翻译成每轮的难度覆盖值。

    本身不碰模型切换 —— 只算出 `decide_override()` 返回的难度 (0=交回路由自行评估,
    2=便宜, 10=强), 由 core/agent.py 的 `_maybe_route_model` 据此调用现有切换逻辑。
    这样策略与切换解耦, 可独立测试, 且不重复 router 的失败保险。
    """

    def __init__(self, config: Any) -> None:
        self.config = config

    # ---- 配置读取 (全部有默认值, 默认关闭, 不影响既有行为) ----
    @property
    def enabled(self) -> bool:
        return bool(self.config.get("router.enabled", False))

    @property
    def plan_execute(self) -> bool:
        return bool(self.config.get("router.plan_execute", False))

    @property
    def escalate(self) -> bool:
        return bool(self.config.get("router.escalate_on_stuck", True))

    @property
    def stuck_threshold(self) -> int:
        return int(self.config.get("router.stuck_threshold", 3) or 3)

    # ---- 状态更新 ----
    def observe_turn(self, session: RouteSession, tool_messages: List[Dict[str, Any]]) -> None:
        """根据本轮工具结果更新卡住计数。

        Args:
            session: 跨 turn 的路由状态 (会被原地修改)。
            tool_messages: 本轮新增的 role=tool 消息列表。
        """
        session.turn += 1
        if not tool_messages:
            # 本轮没有工具调用 (纯文本回复 / 澄清) — 不计入卡住, 也不清零
            return
        failed = [m for m in tool_messages if isinstance(m, dict) and _is_tool_failed(m)]
        if failed:
            session.stuck += 1
            log.debug("路由卡住计数 +1 → %d (阈值 %d)", session.stuck, self.stuck_threshold)
        else:
            if session.stuck:
                log.debug("路由卡住计数清零")
            session.stuck = 0

    # ---- 决策 ----
    def decide_override(self, session: RouteSession) -> int:
        """返回本轮的难度覆盖值 (0=不强制, 2=便宜, 10=强)。

        优先级:
        1. 处于救场窗口 (escalated_until >= 当前 turn) → 10
        2. 规划阶段首轮 (turn==0 且 plan_execute) → 10
        3. 卡住且开启升级 (stuck >= 阈值) → 10, 并开启一轮救场窗口
        4. 执行阶段 (plan_execute) 且未卡住 → 2
        5. 其它 → 0 (交回 router.decide 自行评估)

        注: session.turn 在每轮工具执行后 (observe_turn) 才自增, 因此进入某轮决策时
        turn 仍是「已完成的轮数」—— turn==0 即规划/首轮。
        """
        if session.escalated_until >= session.turn:
            return 10
        if self.plan_execute and session.turn == 0:
            return 10  # 规划: 强模型
        if self.escalate and session.stuck >= self.stuck_threshold:
            session.escalated_until = session.turn  # 救场窗口覆盖紧接着的下一轮
            log.info("便宜模型连续卡住 %d 轮, 升级强模型救场 (救场窗口到 turn=%d)",
                     session.stuck, session.escalated_until)
            return 10
        if self.plan_execute:
            return 2  # 执行: 便宜模型
        return 0


def tool_messages_of_turn(messages: List[Dict[str, Any]], after_index: int) -> List[Dict[str, Any]]:
    """截取本次工具执行后新增的消息 (after_index 含头, 到列表末尾)。"""
    return messages[after_index:] if 0 <= after_index < len(messages) else []
