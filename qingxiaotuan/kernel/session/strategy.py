"""压缩策略配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import inf
from typing import Any, Callable


@dataclass
class CompactionConfig:
    """压缩触发/收缩策略参数。"""

    trigger_ratio: float = 0.85
    block_ratio: float = 0.85
    reserved_context_size: int = 50_000
    max_compaction_per_turn: float = inf
    max_overflow_compaction_attempts: int = 3
    max_recent_messages: int = 4
    max_recent_user_messages: float = inf
    max_recent_size_ratio: float = 0.2
    min_overflow_reduction_ratio: float = 0.05


DEFAULT_COMPACTION_CONFIG = CompactionConfig()
