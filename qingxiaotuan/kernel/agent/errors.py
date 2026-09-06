"""Agent Loop 错误模型 —— 循环层的错误层级与判定。

只保留本子系统关心的两类错误：
- LoopError：loop 自身抛出的错误（带 code 字符串，便于上层分类）。
- LOOP_MAX_STEPS_EXCEEDED：单轮 step 数超过上限（200 默认）。
"""

from __future__ import annotations

from typing import Any


class LoopErrors:
    """错误码表。"""

    codes = {
        "LOOP_MAX_STEPS_EXCEEDED": "loop.max_steps_exceeded",
        "TURN_AGENT_BUSY": "turn.agent_busy",
    }
    # 可重试的码（仅 TURN_AGENT_BUSY，本端口暂未使用，保留语义）
    retryable = ("turn.agent_busy",)


class LoopError(Exception):
    """Loop 层错误基类（Agent、编排、传输错误均可归为它）。"""

    code: str = "loop_error"

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict = details or {}
        self.name = "LoopError"

    def __str__(self) -> str:
        return f"{self.name}[{self.code}]: {self.args[0]}"


def create_max_steps_exceeded_error(max_steps: int, message: str | None = None) -> LoopError:
    """构造「单轮 step 超限」错误。"""
    return LoopError(
        LoopErrors.codes["LOOP_MAX_STEPS_EXCEEDED"],
        message
        or (
            f"Turn exceeded maxSteps={max_steps}. "
            "Raise loop_control.max_steps_per_turn in config, then reload."
        ),
        details={"maxSteps": max_steps},
    )


def is_max_steps_exceeded(err: Any) -> bool:
    """判断错误是否为「max steps 超限」。"""
    return isinstance(err, LoopError) and err.code == LoopErrors.codes["LOOP_MAX_STEPS_EXCEEDED"]


# 兼容任务书里给出的函数名别名
max_steps_exceeded_error = create_max_steps_exceeded_error
is_max_steps_exceeded_error = is_max_steps_exceeded
