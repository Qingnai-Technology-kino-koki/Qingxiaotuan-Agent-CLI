"""多步工具链编排 (Pipeline) —— 让 Agent 一次性组合多个工具自动执行。

核心能力:
- 支持顺序/并行/条件执行
- 工具间依赖关系建模 (上游输出可作为下游输入)
- 每个步骤记录参数、结果、耗时, 供反思使用
- 失败时支持跳过/中止/降级

典型场景:
- 搜索→读取→修改→验证
- 读取文件→分析→生成报告→保存
- git status→分析变更→运行测试→生成摘要
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .base import Tool, ToolContext, string_prop

log = logging.getLogger("qingxiaotuan.pipeline")


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class OnFailure(str, Enum):
    ABORT = "abort"
    SKIP = "skip"
    RETRY = "retry"
    DEGRADE = "degrade"


@dataclass
class PipelineStep:
    id: str
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    on_failure: OnFailure = OnFailure.ABORT
    condition: str = ""
    timeout: int = 120
    output_var: str = ""


@dataclass
class StepResult:
    step_id: str
    tool: str
    status: StepStatus
    output: str = ""
    error: str = ""
    duration: float = 0.0
    args_used: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    steps: List[StepResult] = field(default_factory=list)
    total_duration: float = 0.0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    final_output: str = ""

    @property
    def all_passed(self) -> bool:
        return all(s.status == StepStatus.SUCCESS for s in self.steps)

    def to_report(self) -> str:
        lines = ["Pipeline 执行报告", "=" * 40]
        for s in self.steps:
            icon = {"success": "✓", "failed": "✗", "skipped": "⊘", "pending": "○"}.get(
                s.status.value, "?"
            )
            lines.append(f"  {icon} [{s.step_id}] {s.tool} — {s.status.value} ({s.duration:.1f}s)")
            if s.error:
                lines.append(f"    错误: {s.error[:200]}")
        lines.append(f"\n总计: {self.success_count} 通过, {self.failed_count} 失败, "
                     f"{self.skipped_count} 跳过, 耗时 {self.total_duration:.1f}s")
        if self.final_output:
            lines.append(f"\n最终输出:\n{self.final_output[:1000]}")
        return "\n".join(lines)


class Pipeline:
    """工具链编排引擎。"""

    def __init__(self, registry: Any, ctx: ToolContext) -> None:
        self.registry = registry
        self.ctx = ctx
        self._variables: Dict[str, str] = {}

    def execute(
        self,
        steps: List[PipelineStep],
        on_step_start: Optional[Callable[[str, str], None]] = None,
        on_step_end: Optional[Callable[[str, str, bool], None]] = None,
    ) -> PipelineResult:
        start_time = time.time()
        result = PipelineResult()
        abort = False

        for step in steps:
            if abort:
                result.steps.append(StepResult(
                    step_id=step.id, tool=step.tool, status=StepStatus.SKIPPED,
                    error="Pipeline 已中止",
                ))
                result.skipped_count += 1
                continue

            if step.condition and not self._evaluate_condition(step.condition):
                result.steps.append(StepResult(
                    step_id=step.id, tool=step.tool, status=StepStatus.SKIPPED,
                    error=f"条件不满足: {step.condition}",
                ))
                result.skipped_count += 1
                continue

            resolved_args = self._resolve_variables(step.args)

            if on_step_start:
                on_step_start(step.id, step.tool)

            step_result = self._execute_step(step, resolved_args)

            if on_step_end:
                on_step_end(step.id, step.tool, step_result.status == StepStatus.SUCCESS)

            result.steps.append(step_result)

            if step.output_var and step_result.output:
                self._variables[step.output_var] = step_result.output

            if step_result.status == StepStatus.FAILED:
                result.failed_count += 1
                if step.on_failure == OnFailure.ABORT:
                    abort = True
                elif step.on_failure == OnFailure.RETRY:
                    retry_result = self._execute_step(step, resolved_args)
                    if retry_result.status == StepStatus.SUCCESS:
                        step_result = retry_result
                        result.steps[-1] = retry_result
                        result.failed_count -= 1
                        result.success_count += 1
                        if step.output_var and retry_result.output:
                            self._variables[step.output_var] = retry_result.output
            else:
                result.success_count += 1

        result.total_duration = round(time.time() - start_time, 2)
        result.final_output = self._variables.get("result", "")
        return result

    def _execute_step(self, step: PipelineStep, args: Dict[str, Any]) -> StepResult:
        start = time.time()
        tool = self.registry.get(step.tool)

        if tool is None:
            return StepResult(
                step_id=step.id, tool=step.tool, status=StepStatus.FAILED,
                error=f"未知工具: {step.tool}", args_used=args,
                duration=time.time() - start,
            )

        try:
            output = self.registry.dispatch(step.tool, json.dumps(args, ensure_ascii=False), self.ctx)
            is_error = output.startswith("[错误]") or output.startswith("[已拒绝]")
            return StepResult(
                step_id=step.id, tool=step.tool,
                status=StepStatus.FAILED if is_error else StepStatus.SUCCESS,
                output=output, args_used=args,
                duration=round(time.time() - start, 2),
                error=output if is_error else "",
            )
        except Exception as exc:
            return StepResult(
                step_id=step.id, tool=step.tool, status=StepStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}", args_used=args,
                duration=round(time.time() - start, 2),
            )

    def _resolve_variables(self, args: Dict[str, Any]) -> Dict[str, Any]:
        resolved: Dict[str, Any] = {}
        for key, value in args.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                var_name = value[2:-1]
                resolved[key] = self._variables.get(var_name, value)
            elif isinstance(value, dict):
                resolved[key] = self._resolve_variables(value)
            else:
                resolved[key] = value
        return resolved

    def _evaluate_condition(self, condition: str) -> bool:
        parts = condition.split(None, 2)
        if len(parts) < 2:
            return True
        var_name = parts[0]
        operator = parts[1]
        operand = parts[2] if len(parts) > 2 else ""
        value = self._variables.get(var_name, "")
        if operator == "contains":
            return operand in value
        elif operator == "not_empty":
            return bool(value.strip())
        elif operator == "empty":
            return not value.strip()
        elif operator == "equals":
            return value == operand
        return True


def pipeline_run(
    ctx: ToolContext,
    steps_json: str = "[]",
    description: str = "",
) -> str:
    """执行一个多步工具链。"""
    try:
        steps_data = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
    except json.JSONDecodeError as exc:
        return f"[错误] steps 不是合法 JSON: {exc}"

    steps: List[PipelineStep] = []
    for i, s in enumerate(steps_data):
        steps.append(PipelineStep(
            id=s.get("id", f"step_{i+1}"),
            tool=s.get("tool", ""),
            args=s.get("args", {}),
            description=s.get("description", ""),
            on_failure=OnFailure(s.get("on_failure", "abort")),
            condition=s.get("condition", ""),
            output_var=s.get("output_var", ""),
            timeout=s.get("timeout", 120),
        ))

    if not steps:
        return "[错误] 步骤列表为空"

    pipeline = Pipeline(ctx.kernel.require("tool_registry"), ctx)
    result = pipeline.execute(steps)
    return result.to_report()


def pipeline_list_tools(ctx: ToolContext) -> str:
    """列出所有可用工具及其参数 schema。"""
    registry = ctx.kernel.require("tool_registry")
    tools_info: List[str] = []
    for tool in registry.tools:
        params = json.dumps(tool.parameters, ensure_ascii=False, indent=2)
        tools_info.append(f"### {tool.name}\n{tool.description}\n参数:\n```json\n{params}\n```")
    return "\n\n".join(tools_info)


class PipelinePlugin:
    """Pipeline 工具插件 (需继承 Plugin)。"""

    def __init__(self):
        pass

    def activate(self, kernel):
        pass


# 动态继承 Plugin
from ..core.kernel import Plugin as _Plugin  # noqa: E402


class PipelinePlugin(_Plugin):
    name = "tools.pipeline"
    requires = ["tool_registry"]

    def activate(self, kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="pipeline_run",
            description="执行多步工具链: 顺序/并行/条件执行, 支持步骤间变量传递",
            parameters={
                "type": "object",
                "properties": {
                    "steps_json": {
                        "type": "string",
                        "description": "JSON 格式的步骤列表, 每个步骤: {id, tool, args, on_failure, condition, output_var}",
                    },
                    "description": string_prop("Pipeline 描述"),
                },
                "required": ["steps_json"],
            },
            handler=pipeline_run, group="orchestration",
        ))
        registry.register(Tool(
            name="pipeline_list_tools",
            description="列出所有可用工具及其参数 schema, 供构建 pipeline 时参考",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=pipeline_list_tools, group="orchestration",
        ))
