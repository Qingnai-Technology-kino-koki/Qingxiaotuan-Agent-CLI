"""gate —— 权限闸门：在工具真正执行前拦截/询问/放行。

``PermissionGate`` 订阅执行器的 ``on_before_execute`` 钩子，在每次工具真正执行前做裁决：
- policy 返回 approve -> event.pass()（放行）
- policy 返回 deny   -> event.veto()（否决，工具不执行）
- policy 返回 ask    -> event.wait_until(factory)，阻塞等到人工/异步裁决结果：
  approved 则放行，rejected/cancelled 则否决。

人工裁决 UI 不在此实现，仅定义 ``ApprovalService`` 协议；测试可注入桩。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from .before_execute_event import BeforeExecuteDecision, BeforeToolExecuteEvent
from .contract import ExecutableToolResult
from .executor import ToolExecutor
from .permission import (
    PermissionDecision,
    PermissionMode,
    PermissionPolicyEvaluation,
    PermissionPolicyResult,
    PermissionPolicyService,
)


def deny_tool_execution(reason: str) -> ExecutableToolResult:
    """构造一个「被否决」的工具结果。"""
    return ExecutableToolResult(output=reason, is_error=True)


@dataclass
class ApprovalRequest:
    """提交给人审服务的审批请求（简化 port，不含 UI 细节）。"""

    tool_call_id: str
    tool_name: str
    action: str
    display: Any = None
    tool_input: Any = None
    policy_name: str = ""
    id: Optional[str] = None


@runtime_checkable
class ApprovalService(Protocol):
    """人审服务协议：仅需实现 request_approval。

    返回：
    - None / 不含 veto 的 BeforeExecuteDecision -> 批准（放行）
    - 含 veto 的 BeforeExecuteDecision        -> 否决（工具不执行）
    """

    async def request_approval(
        self,
        event: BeforeToolExecuteEvent,
        ask_result: PermissionPolicyResult,
        policy_name: str,
    ) -> Optional[BeforeExecuteDecision]:
        ...


class PermissionGate:
    """权限闸门：订阅执行器 before_execute，按策略链裁决。"""

    def __init__(
        self,
        executor: ToolExecutor,
        policy_service: Optional[PermissionPolicyService] = None,
        approval_service: Optional[ApprovalService] = None,
        mode: PermissionMode = PermissionMode.MANUAL,
    ) -> None:
        self.executor = executor
        self.policy_service = policy_service or PermissionPolicyService(mode=mode)
        self.approval_service = approval_service
        self._disposable = executor.subscribe_before_execute(self.adjudicate)

    def dispose(self) -> None:
        self._disposable.dispose()

    # ---- 裁决 ---------------------------------------------------------------

    async def adjudicate(self, event: BeforeToolExecuteEvent) -> None:
        evaluation = await self.policy_service.evaluate(event)
        if evaluation is None:
            return
        result = evaluation.result
        if result.kind == PermissionDecision.ASK:
            event.wait_until(
                lambda: self.request_tool_approval(event, result, evaluation.policy_name)
            )
            return
        if result.kind == PermissionDecision.APPROVE:
            event.pass_metadata(result.execution_metadata)
            return
        # deny
        event.veto(
            deny_tool_execution(
                result.message
                or f'Tool "{event.tool_call.name}" was denied by permission policy.'
            )
        )

    async def request_tool_approval(
        self,
        event: BeforeToolExecuteEvent,
        ask_result: PermissionPolicyResult,
        policy_name: str,
    ) -> Optional[BeforeExecuteDecision]:
        """构造审批请求并等待裁决；approved 返回 None，否则返回带 veto 的决策。"""
        request = ApprovalRequest(
            tool_call_id=event.tool_call.id,
            tool_name=event.tool_call.name,
            action=event.execution.description or f"Approve {event.tool_call.name}",
            display=getattr(event.execution, "display", None),
            tool_input=event.args,
            policy_name=policy_name,
        )
        if self.approval_service is None:
            # 没有审批服务时按批准处理（等价于 TS 中 approvalService===undefined 分支）
            return None
        return await self.approval_service.request_approval(event, ask_result, policy_name)


__all__ = [
    "PermissionGate",
    "ApprovalService",
    "ApprovalRequest",
    "deny_tool_execution",
]
