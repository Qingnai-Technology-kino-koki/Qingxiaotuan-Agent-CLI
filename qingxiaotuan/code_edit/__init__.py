"""qingxiaotuan.code_edit —— 代码编辑助手的可执行模块。

把「代码编辑助手提示词」里可落地的机制落成代码：
- task_spec：标准化四要素输入模板（Goal/Context/Constraints/Done when）
- safety_gate：五层防误操作 + 版本锁死 + 权限回收
- verify_loop：预测—反馈闭环（改完跑测试，失败诊断重试）
- rules：规则外置（项目规则自动加载，记忆只存用户偏好）
- assistant：编排器（依赖注入，便于测试与不同承载环境复用）
"""

from .assistant import CodeEditAssistant, EditReport
from .rules import UserPrefsMemory, load_project_rules
from .safety_gate import (Action, AuditEvent, PermissionGrant, RiskLevel,
                          SafetyGate, check_version_lock, classify_risk)
from .task_spec import TEMPLATE, TaskSpec, parse_task_spec
from .verify_loop import TestResult, VerifyLoop, run_tests

__all__ = [
    "CodeEditAssistant", "EditReport",
    "TaskSpec", "parse_task_spec", "TEMPLATE",
    "SafetyGate", "Action", "RiskLevel", "AuditEvent", "PermissionGrant",
    "classify_risk", "check_version_lock",
    "VerifyLoop", "run_tests", "TestResult",
    "load_project_rules", "UserPrefsMemory",
]
