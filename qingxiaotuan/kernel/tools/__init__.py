"""kernel.tools —— 青小团自研的工具系统 + 权限子系统。

子模块：
- contract       : 可执行工具抽象（ExecutableTool / RunnableToolExecution / 结果 / ToolAccesses）
- registry       : 工具注册表（支持 source 标记与反注册）
- scheduler      : asyncio 细粒度调度（读并行 / 写串行 / 路径重叠串行）
- executor       : ToolExecutor（preflight -> prepare -> 调度 -> yield）
- before_execute_event : 执行前事件（veto/allow/pass/wait_until 异步语义）
- permission     : 权限模式 / 策略链 / 内置策略 / match_permission_rule
- gate           : PermissionGate（订阅 before_execute 做裁决）+ ApprovalService 协议
- bridge         : wrap_existing_tool / KernelToolExecutorAdapter 桥接现有工具系统
- args           : 参数容错解析 / 轻量 schema 校验 / 路径安全
"""

from .args import (
    PathSecurityError,
    is_sensitive_file,
    parse_tool_call_arguments,
    validate_tool_args,
)
from .before_execute_event import BeforeExecuteDecision, BeforeToolExecuteEvent
from .bridge import KernelToolExecutorAdapter, wrap_existing_tool
from .contract import (
    ExecutableTool,
    ExecutableToolContext,
    ExecutableToolResult,
    RunnableToolExecution,
    ToolAccesses,
    ToolDefinition,
    ToolInfo,
    ToolResourceAccess,
    ToolSource,
)
from .executor import ToolExecutionResult, ToolExecutor
from .gate import ApprovalRequest, ApprovalService, PermissionGate, deny_tool_execution
from .permission import (
    AutoModeApprovePolicy,
    DefaultToolApprovePolicy,
    FallbackAskPolicy,
    PermissionDecision,
    PermissionMode,
    PermissionPolicy,
    PermissionPolicyEvaluation,
    PermissionPolicyResult,
    PermissionPolicyService,
    SensitiveFileAccessAskPolicy,
    UserConfiguredRulePolicy,
    YoloModeApprovePolicy,
    match_permission_rule,
)
from .registry import ToolRegistry, default_registry, register_tool
from .scheduler import SchedulerTask, ToolScheduler

__all__ = [
    # contract
    "ExecutableTool",
    "ExecutableToolContext",
    "ExecutableToolResult",
    "RunnableToolExecution",
    "ToolAccesses",
    "ToolResourceAccess",
    "ToolDefinition",
    "ToolInfo",
    "ToolSource",
    # registry
    "ToolRegistry",
    "default_registry",
    "register_tool",
    # scheduler
    "ToolScheduler",
    "SchedulerTask",
    # executor
    "ToolExecutor",
    "ToolExecutionResult",
    # before-execute event
    "BeforeToolExecuteEvent",
    "BeforeExecuteDecision",
    # permission
    "PermissionMode",
    "PermissionDecision",
    "PermissionPolicy",
    "PermissionPolicyResult",
    "PermissionPolicyService",
    "PermissionPolicyEvaluation",
    "AutoModeApprovePolicy",
    "YoloModeApprovePolicy",
    "SensitiveFileAccessAskPolicy",
    "DefaultToolApprovePolicy",
    "FallbackAskPolicy",
    "UserConfiguredRulePolicy",
    "match_permission_rule",
    # gate
    "PermissionGate",
    "ApprovalService",
    "ApprovalRequest",
    "deny_tool_execution",
    # args
    "parse_tool_call_arguments",
    "validate_tool_args",
    "PathSecurityError",
    "is_sensitive_file",
    # bridge
    "wrap_existing_tool",
    "KernelToolExecutorAdapter",
]
