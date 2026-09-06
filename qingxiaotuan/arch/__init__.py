"""青小团 · 五层架构 (Five-Layer Architecture)

把分散在各处的子系统收敛成一套语义清晰、可插拔、可测试的架构:

    security       安全层  — OS 级沙箱 (syscall 级拦截) + 不可变安全策略 + 密码学级加密
    execution      执行层  — Agent Loop 可插拔 + 工具管线 waterfall (pre/post) + 5 种事件语义
    orchestration  编排层  — 5 子代理并发 + 3 层嵌套 + 独立上下文/工作树 + 自动冲突解决
    context        上下文层— 1M 上下文缓存友好 + 事件溯源 + 分叉重放 + 自动技能蒸馏
    observability  可观测层— Trajectory 视图 + 每步"模型看到了什么" + 跨会话归因

所有模块都尽量复用既有基建 (ext.crypto_engine / core.sandbox / core.event_sourcer /
core.trajectory / self_improve.auto_distiller), 仅在既有能力不足以覆盖规格时才新增实现。
"""

from .security import (
    SyscallSandbox,
    ImmutableSecurityPolicy,
    CryptoVault,
    PolicyViolation,
)
from .execution import (
    BaseAgentLoop,
    LoopContext,
    LoopResult,
    ToolPipeline,
    SemanticEvent,
    SemanticBus,
    EventSemantics,
    LoopProviderBridge,
)
from .orchestration import (
    Orchestrator,
    SubAgent,
    AgentSpec,
    AgentContext,
    SubAgentResult,
    ConflictDetector,
    ConflictResolver,
    HumanApprovalGate,
    Resolution,
    kernel_executor,
)
from .context import (
    TieredContext,
    ContextEventLog,
    fork_and_replay,
    SkillDistiller,
)
from .observability import (
    TrajectoryStore,
    StepViewer,
    CrossSessionAttributor,
)

__all__ = [
    "SyscallSandbox",
    "ImmutableSecurityPolicy",
    "CryptoVault",
    "PolicyViolation",
    "BaseAgentLoop",
    "LoopContext",
    "LoopResult",
    "ToolPipeline",
    "SemanticEvent",
    "SemanticBus",
    "EventSemantics",
    "LoopProviderBridge",
    "Orchestrator",
    "SubAgent",
    "AgentSpec",
    "AgentContext",
    "SubAgentResult",
    "ConflictDetector",
    "ConflictResolver",
    "HumanApprovalGate",
    "Resolution",
    "kernel_executor",
    "TieredContext",
    "ContextEventLog",
    "fork_and_replay",
    "SkillDistiller",
    "TrajectoryStore",
    "StepViewer",
    "CrossSessionAttributor",
]
