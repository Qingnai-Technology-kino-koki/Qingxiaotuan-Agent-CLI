"""Loop 控制参数 —— 循环的最大步数、超时等约束。

环境变量：
- KIMI_LOOP_MAX_STEPS_PER_TURN   （默认 200）
- KIMI_LOOP_MAX_ATTEMPTS_PER_STEP（默认 6，KIMI_LOOP_MAX_RETRIES_PER_STEP 为已废弃别名）
- KIMI_LOOP_MAX_RETRIES_PER_STEP  （已废弃，等同 max_attempts_per_step）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

LOOP_MAX_STEPS_PER_TURN_ENV = "KIMI_LOOP_MAX_STEPS_PER_TURN"
LOOP_MAX_ATTEMPTS_PER_STEP_ENV = "KIMI_LOOP_MAX_ATTEMPTS_PER_STEP"
LOOP_MAX_RETRIES_PER_STEP_ENV = "KIMI_LOOP_MAX_RETRIES_PER_STEP"


@dataclass
class LoopControl:
    max_steps_per_turn: int = 200
    max_attempts_per_step: int = 6
    max_ralph_iterations: int = -1
    reserved_context_size: int = 0
    compaction_trigger_ratio: float = 0.8

    @staticmethod
    def defaults() -> "LoopControl":
        return LoopControl()


def _parse_non_negative_int(raw: str) -> Optional[int]:
    value = raw.strip()
    if not value or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed >= 0 else None


def loop_control_from_env() -> LoopControl:
    """从环境变量读取 LoopControl；未设置则使用默认值。"""
    ctrl = LoopControl()

    steps = os.environ.get(LOOP_MAX_STEPS_PER_TURN_ENV)
    if steps is not None:
        parsed = _parse_non_negative_int(steps)
        if parsed is not None:
            ctrl.max_steps_per_turn = parsed

    # max_attempts_per_step 优先，废弃别名回退
    attempts = os.environ.get(LOOP_MAX_ATTEMPTS_PER_STEP_ENV)
    if attempts is None:
        attempts = os.environ.get(LOOP_MAX_RETRIES_PER_STEP_ENV)
    if attempts is not None:
        parsed = _parse_non_negative_int(attempts)
        if parsed is not None:
            ctrl.max_attempts_per_step = parsed

    return ctrl
