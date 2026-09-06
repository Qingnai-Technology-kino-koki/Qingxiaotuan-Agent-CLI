"""permission —— 工具执行的权限策略子系统
(``permissionPolicy/*``、``permissionMode/*``、``toolApproval/*`` 的简化合并)。

要点：
- ``PermissionMode``：manual / yolo / auto。
- ``PermissionDecision``：approve / deny / ask。
- ``PermissionPolicy`` 协议：``evaluate(ctx) -> PermissionPolicyResult | None``（None 表示
  「本策略不关心，交给下一个」）。
- ``PermissionPolicyService``：策略链顺序评估，首个非 None 生效。
- 内置若干策略：auto_approve / yolo_approve / sensitive_file_access_ask /
  user_configured_rule / default_tool_approve / fallback_ask。
- ``match_permission_rule``：toolName + args/路径 的 glob 匹配（简化 port）。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from .args import is_sensitive_file, matches_glob_rule_subject
from .before_execute_event import BeforeToolExecuteEvent
from .contract import ToolAccesses, ToolResourceAccess

# 权限上下文 == 执行前事件（含 tool_call / args / execution.accesses）
PolicyContext = BeforeToolExecuteEvent


class PermissionMode(enum.Enum):
    MANUAL = "manual"
    YOLO = "yolo"
    AUTO = "auto"

    def __str__(self) -> str:  # 便于直接比较/打印
        return self.value


class PermissionDecision(str, enum.Enum):
    APPROVE = "approve"
    DENY = "deny"
    ASK = "ask"

    def __str__(self) -> str:
        return self.value


@dataclass
class PermissionPolicyResult:
    """策略评估结果。

    - approve: 放行（可附带 execution_metadata）
    - deny:    拒绝（message 为拒绝原因）
    - ask:     需人工裁决（resolve_approval / resolve_error 可选，由 gate 直接走 approval_service）
    """

    kind: PermissionDecision
    reason: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    execution_metadata: Any = None
    resolve_approval: Optional[Callable[..., Any]] = None
    resolve_error: Optional[Callable[..., Any]] = None


@runtime_checkable
class PermissionPolicy(Protocol):
    name: str

    def evaluate(self, context: PolicyContext) -> "PermissionPolicyResult | None | Awaitable[PermissionPolicyResult | None]":
        ...


# ---------------------------------------------------------------- 模式持有者

class _ModeHolder:
    def __init__(self, mode: PermissionMode = PermissionMode.MANUAL) -> None:
        self.mode = mode


# ---------------------------------------------------------------- 内置策略


class AutoModeApprovePolicy:
    name = "auto-mode-approve"

    def __init__(self, mode: _ModeHolder) -> None:
        self._mode = mode

    def evaluate(self, context: PolicyContext) -> Optional[PermissionPolicyResult]:
        if self._mode.mode == PermissionMode.AUTO:
            return PermissionPolicyResult(kind=PermissionDecision.APPROVE)
        return None


class YoloModeApprovePolicy:
    name = "yolo-mode-approve"

    def __init__(self, mode: _ModeHolder) -> None:
        self._mode = mode

    def evaluate(self, context: PolicyContext) -> Optional[PermissionPolicyResult]:
        if self._mode.mode == PermissionMode.YOLO:
            return PermissionPolicyResult(kind=PermissionDecision.APPROVE)
        return None


class SensitiveFileAccessAskPolicy:
    name = "sensitive-file-access-ask"

    def evaluate(self, context: PolicyContext) -> Optional[PermissionPolicyResult]:
        accesses = context.execution.accesses
        for access in accesses:
            if access.kind == "file" and is_sensitive_file(access.path):
                return PermissionPolicyResult(
                    kind=PermissionDecision.ASK,
                    reason={"path": access.path},
                )
        return None


# 默认放行的只读/查询类工具（对应 TS default-tool-approve 的白名单思路）。
_DEFAULT_APPROVE_TOOLS = {
    "read_file",
    "read_files",
    "glob",
    "list_directory",
    "git_status",
    "git_log",
    "git_diff",
    "web_fetch",
    "web_search",
    "memory_search",
    "code_search",
    "skill_list",
}


class DefaultToolApprovePolicy:
    name = "default-tool-approve"

    def evaluate(self, context: PolicyContext) -> Optional[PermissionPolicyResult]:
        if context.tool_call.name in _DEFAULT_APPROVE_TOOLS:
            return PermissionPolicyResult(kind=PermissionDecision.APPROVE)
        return None


class FallbackAskPolicy:
    name = "fallback-ask"

    def evaluate(self, context: PolicyContext) -> PermissionPolicyResult:
        return PermissionPolicyResult(kind=PermissionDecision.ASK)


# ---------------------------------------------------------------- 用户规则匹配

def _args_subject(args: Any) -> str:
    """把参数摊平成用于 glob 匹配的字符串（简化）。"""
    if isinstance(args, dict):
        return " ".join(str(v) for v in args.values())
    return str(args)


def match_permission_rule(
    rule: Dict[str, Any],
    tool_name: str,
    args: Any,
    accesses: ToolAccesses,
) -> bool:
    """判断单条用户规则是否命中当前调用。

    - tool：glob 匹配工具名
    - pattern：可选 glob，匹配路径类访问或参数摊平字符串；空则命中
    - 支持 ! 取反（matches_glob_rule_subject 内部处理）
    """
    import fnmatch

    tool_pattern = str(rule.get("tool", "*"))
    if not fnmatch.fnmatch(tool_name.lower(), tool_pattern.lower()):
        return False
    pattern = rule.get("pattern")
    if not pattern:
        return True
    subjects: List[str] = []
    for access in accesses:
        if isinstance(access, ToolResourceAccess) and access.kind == "file":
            subjects.append(access.path)
    subjects.append(_args_subject(args))
    return any(matches_glob_rule_subject(str(pattern), s) for s in subjects)


class UserConfiguredRulePolicy:
    name = "user-configured-rule"

    def __init__(self, rules: List[Dict[str, Any]]) -> None:
        self.rules = rules

    def evaluate(self, context: PolicyContext) -> Optional[PermissionPolicyResult]:
        decision = context.__dict__.get("_user_rule_decision")
        # 若上下文已带决策（由 gate 注入），直接复用
        if isinstance(decision, PermissionDecision):
            if decision == PermissionDecision.DENY:
                return PermissionPolicyResult(
                    kind=PermissionDecision.DENY,
                    message=f"Tool \"{context.tool_call.name}\" was denied by permission rule.",
                )
            if decision == PermissionDecision.ASK:
                return PermissionPolicyResult(kind=PermissionDecision.ASK)
            return PermissionPolicyResult(kind=PermissionDecision.APPROVE)
        for rule in self.rules:
            action = str(rule.get("action", "")).lower()
            if action not in ("allow", "deny", "ask"):
                continue
            if not match_permission_rule(
                rule, context.tool_call.name, context.args, context.execution.accesses
            ):
                continue
            if action == "deny":
                return PermissionPolicyResult(
                    kind=PermissionDecision.DENY,
                    message=f"Tool \"{context.tool_call.name}\" was denied by permission rule.",
                )
            if action == "ask":
                return PermissionPolicyResult(kind=PermissionDecision.ASK)
            return PermissionPolicyResult(kind=PermissionDecision.APPROVE)
        return None


# ---------------------------------------------------------------- 策略链服务


@dataclass
class PermissionPolicyEvaluation:
    policy_name: str
    result: PermissionPolicyResult


class PermissionPolicyService:
    """策略链：按顺序评估，首个非 None 结果生效。"""

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.MANUAL,
        rules: Optional[List[Dict[str, Any]]] = None,
        policies: Optional[List[Any]] = None,
    ) -> None:
        self.mode_holder = _ModeHolder(mode)
        self.rules = list(rules or [])
        if policies is not None:
            self.policies: List[Any] = list(policies)
        else:
            self.policies = [
                AutoModeApprovePolicy(self.mode_holder),
                YoloModeApprovePolicy(self.mode_holder),
                SensitiveFileAccessAskPolicy(),
                UserConfiguredRulePolicy(self.rules),
                DefaultToolApprovePolicy(),
                FallbackAskPolicy(),
            ]

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode_holder.mode = mode

    async def evaluate(self, context: PolicyContext) -> Optional[PermissionPolicyEvaluation]:
        for policy in self.policies:
            result = policy.evaluate(context)
            if hasattr(result, "__await__"):
                result = await result
            if result is not None:
                return PermissionPolicyEvaluation(policy_name=policy.name, result=result)
        return None


__all__ = [
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
]
