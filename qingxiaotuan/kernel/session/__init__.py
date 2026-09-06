"""qingxiaotuan.kernel.session —— 会话与上下文压缩/记忆子系统。

导出核心 API：
- ContextMemory：内存上下文存储器（get/append/append_loop_event/undo/apply_compaction/clear）
- WireStore：append-only JSONL 持久化 + replay 重建
- FullCompaction：压缩触发判定 + 用注入函数生成摘要并执行压缩
- build_messages：组装 [system] + history
- Projector：上下文投影（strict 模式做 tool 邻接修正）

辅助导出：compaction 估算/选择函数、契约类型（ContextMessage / Origin / LoopRecordedEvent）、
桥接层（KernelSessionBridge / create_session_memory）。
"""

from .bridge import KernelSessionBridge, create_session_memory
from .compaction import (
    COMPACT_HEAD_TOKENS,
    COMPACT_MAX_TOKENS,
    DEFAULT_COMPACTION_CONFIG,
    FullCompaction,
    build_compaction_shape,
    estimate_tokens,
    estimate_tokens_for_message,
    estimate_tokens_for_messages,
    select_compaction_user_messages,
)
from .contracts import (
    CompactionInput,
    CompactionResult,
    ContextMessage,
    LoopRecordedEvent,
    LoopToolResult,
    Origin,
    OriginKind,
)
from .memory import ContextMemory, build_messages, compute_undo_cut
from .projector import ProjectionPolicy, Projector
from .strategy import CompactionConfig
from .wire import WireStore, replay, replay_file

__all__ = [
    # 核心
    "ContextMemory",
    "WireStore",
    "FullCompaction",
    "build_messages",
    "Projector",
    # compaction 工具
    "estimate_tokens",
    "estimate_tokens_for_message",
    "estimate_tokens_for_messages",
    "select_compaction_user_messages",
    "build_compaction_shape",
    "COMPACT_HEAD_TOKENS",
    "COMPACT_MAX_TOKENS",
    "DEFAULT_COMPACTION_CONFIG",
    "CompactionConfig",
    # 契约
    "ContextMessage",
    "Origin",
    "OriginKind",
    "LoopRecordedEvent",
    "LoopToolResult",
    "CompactionInput",
    "CompactionResult",
    # projector
    "ProjectionPolicy",
    # wire
    "replay",
    "replay_file",
    # bridge
    "KernelSessionBridge",
    "create_session_memory",
    "compute_undo_cut",
]
