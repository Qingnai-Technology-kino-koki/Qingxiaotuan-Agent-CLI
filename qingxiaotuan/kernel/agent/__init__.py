"""kernel.agent —— Agent 编排/循环 子系统）。

导出任务书要求的核心 API：
- AgentLoopService：单 turn 内的 ReAct 主循环。
- LoopRunResult：循环运行结果。
- StepRequest：step 请求抽象（含 MessageStepRequest / ContinuationStepRequest）。
- LoopErrorHandler：注册式错误恢复处理器。
- run_agent_loop：便捷函数，跑完整 ReAct 循环并返回最终 assistant Message。

另导出errors/config/step_queue/continuation/step_retry/bridge 等子模块的关键类型。
"""

from __future__ import annotations

from .bridge import KernelAgentLoopAdapter, run_agent_loop
from .config import LoopControl, loop_control_from_env
from .continuation import LoopContinuationService
from .errors import (
    LoopError,
    LoopErrors,
    create_max_steps_exceeded_error,
    is_max_steps_exceeded,
    max_steps_exceeded_error,
)
from .loop import (
    AgentLoopService,
    LoopErrorContext,
    LoopErrorHandler,
    LoopRunOptions,
    LoopRunResult,
    LoopStreamEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolExecutionResult,
    ToolExecutor,
    create_stream_part_handler,
)
from .step_queue import StepRequestBatch, StepRequestQueue
from .step_request import (
    ContinuationStepRequest,
    MessageStepRequest,
    StepRequest,
    StepRequestOptions,
)
from .step_retry import StepRetryService, is_retryable_error

__all__ = [
    # 任务书要求导出
    "AgentLoopService",
    "LoopRunResult",
    "StepRequest",
    "LoopErrorHandler",
    "run_agent_loop",
    # 其它
    "KernelAgentLoopAdapter",
    "LoopContinuationService",
    "StepRetryService",
    "LoopControl",
    "loop_control_from_env",
    "LoopError",
    "LoopErrors",
    "create_max_steps_exceeded_error",
    "max_steps_exceeded_error",
    "is_max_steps_exceeded",
    "LoopErrorContext",
    "LoopRunOptions",
    "LoopStreamEvent",
    "TextEvent",
    "ThinkingEvent",
    "ToolCallEvent",
    "ToolExecutionResult",
    "ToolExecutor",
    "create_stream_part_handler",
    "StepRequestBatch",
    "StepRequestQueue",
    "ContinuationStepRequest",
    "MessageStepRequest",
    "StepRequestOptions",
    "is_retryable_error",
]
