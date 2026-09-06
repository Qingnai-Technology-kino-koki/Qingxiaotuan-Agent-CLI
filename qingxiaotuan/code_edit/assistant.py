"""CodeEditAssistant：把提示词的流程落成可调用编排器。

编排顺序（与提示词一致）：
  解析任务(四要素) → 缺失则一次问询补全 → 加载项目规则 → 五层安全闸门
  → 委托编辑(edit_fn) → 预测—反馈闭环(verify) → 结构化报告。

所有外部副作用（问询/确认/编辑/跑测试）通过回调注入，便于单测与不同承载环境复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .rules import UserPrefsMemory, load_project_rules
from .safety_gate import Action, RiskLevel, SafetyGate, check_version_lock
from .task_spec import TaskSpec, parse_task_spec
from .verify_loop import TestResult, VerifyLoop, run_tests


@dataclass
class EditReport:
    spec: TaskSpec
    project_rules_loaded: bool = False
    preview_shown: bool = False
    confirmed: bool = False
    edit_notes: str = ""
    verify: Optional[TestResult] = None
    warnings: list = field(default_factory=list)
    denied: bool = False
    deny_reason: str = ""

    def render(self) -> str:
        lines = ["# 代码编辑执行报告", ""]
        if self.denied:
            lines.append(f"⛔ 已拦截: {self.deny_reason}")
            return "\n".join(lines)
        lines.append("## 任务简报")
        lines.append(self.spec.render())
        if self.project_rules_loaded:
            lines.append("\n(已自动加载项目级规则)")
        lines.append(f"\n## 安全闸门\n预览已展示: {self.preview_shown} | 已确认: {self.confirmed}")
        for w in self.warnings:
            lines.append(f"  - 复核提示: {w}")
        lines.append(f"\n## 编辑说明\n{self.edit_notes or '(由 Agent 执行)'}")
        if self.verify is not None:
            lines.append(f"\n## 验证结果\n{self.verify.summary()}")
        return "\n".join(lines)


# 回调类型
AskFn = Callable[[list], dict]            # 缺失字段 -> 补全的 partial dict
ConfirmFn = Callable[[str], bool]         # 预览文本 -> 是否放行
EditFn = Callable[[TaskSpec, str], str]   # (spec, rules) -> 编辑说明
RunTestFn = Callable[[str, Optional[str]], TestResult]


class CodeEditAssistant:
    def __init__(
        self,
        *,
        ask_fn: Optional[AskFn] = None,
        confirm_fn: Optional[ConfirmFn] = None,
        edit_fn: Optional[EditFn] = None,
        run_test_fn: Optional[RunTestFn] = None,
        workspace: str = ".",
        max_verify_retries: int = 3,
        version_strategy: str = "semver",
        prefs: Optional[UserPrefsMemory] = None,
    ):
        self.workspace = workspace
        self.version_strategy = version_strategy
        self._ask_fn = ask_fn
        self._edit_fn = edit_fn
        self._prefs = prefs or UserPrefsMemory()
        self._gate = SafetyGate(confirm_fn=confirm_fn)
        self._loop = VerifyLoop(run_fn=run_test_fn, max_retries=max_verify_retries)

    # 暴露内部构件，便于承载层按需增强
    @property
    def gate(self) -> SafetyGate:
        return self._gate

    @property
    def prefs(self) -> UserPrefsMemory:
        return self._prefs

    def plan(self, raw_task: str, partial: Optional[dict] = None) -> TaskSpec:
        """解析并补全任务（不执行编辑/验证）。"""
        spec = parse_task_spec(raw_task, partial)
        if not spec.is_complete() and self._ask_fn is not None:
            filled = self._ask_fn(spec.missing())
            for k, v in (filled or {}).items():
                if k in TaskSpec.REQUIRED and v:
                    setattr(spec, k, v)
        return spec

    def run(
        self,
        raw_task: str,
        test_cmd: Optional[str] = None,
        partial: Optional[dict] = None,
        action: Optional[Action] = None,
    ) -> EditReport:
        spec = self.plan(raw_task, partial)
        report = EditReport(spec=spec)
        report.project_rules_loaded = load_project_rules(self.workspace).strip() != ""

        # 五层闸门：有动作描述才走完整闸门
        if action is not None:
            preview = self._gate.preview(action)
            report.preview_shown = True
            report.warnings = self._gate.recheck(action)
            if self._gate.requires_confirmation(action) and not self._gate.confirm(action, preview):
                report.denied = True
                report.deny_reason = f"{action.kind} 未获确认，按五层防误操作拦截"
                return report
            report.confirmed = True
            # 版本锁死
            if action.kind == "bump_version":
                ok, reason = check_version_lock(
                    action.version_old, action.version_new, self.version_strategy
                )
                if not ok:
                    report.denied = True
                    report.deny_reason = f"版本锁死: {reason}"
                    return report
                self._gate.audit("bump_version", reason)

        # 委托编辑（真实编辑由 Agent/LLM 完成，此处只记录说明）
        if self._edit_fn is not None:
            report.edit_notes = self._edit_fn(spec, load_project_rules(self.workspace))
        else:
            report.edit_notes = "（请按上方标准化任务简报进行编辑）"

        # 预测—反馈闭环
        if test_cmd:
            report.verify = self._loop.run(test_cmd, cwd=self.workspace)
        return report
