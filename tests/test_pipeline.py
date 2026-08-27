"""多步工具链编排 (Pipeline) 单元测试。"""

import json

from qingxiaotuan.tools.base import ToolContext
from qingxiaotuan.tools.pipeline import (
    OnFailure,
    Pipeline,
    PipelineResult,
    PipelineStep,
    StepStatus,
    pipeline_list_tools,
    pipeline_run,
)


class FakeRegistry:
    """最小化工具注册表替身: 记录调用, 按脚本返回结果。"""

    def __init__(self, results=None, tools=None):
        self._results = results or {}
        self._tools = tools or {}
        self.calls = []

    def get(self, name):
        # 有结果条目的工具视为已注册 (返回占位对象), 否则视为未知工具
        if name in self._results:
            return self._tools.get(name) or object()
        return self._tools.get(name)

    def dispatch(self, name, arguments_json, ctx):
        self.calls.append((name, json.loads(arguments_json)))
        return self._results.get(name, "ok")


class ScriptedRegistry(FakeRegistry):
    """按调用顺序返回脚本结果, 用于测试重试/跳过等失败场景。"""

    def __init__(self, script):
        super().__init__()
        self._script = list(script)

    def get(self, name):
        return object()  # 脚本化测试只使用已知工具

    def dispatch(self, name, arguments_json, ctx):
        self.calls.append((name, json.loads(arguments_json)))
        return self._script.pop(0) if self._script else "ok"


def _ctx():
    return ToolContext(kernel=None, workspace=".")


def _steps(*specs):
    return [PipelineStep(**s) for s in specs]


# ------------------------------------------------------------------ 顺序执行与变量传递

def test_sequence_execution_and_variable_passing():
    """上游输出应通过 output_var 传递给下游 ${var} 参数。"""
    registry = FakeRegistry(results={"echo": "hello"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "hello"}, "output_var": "msg"},
        {"id": "s2", "tool": "echo", "args": {"text": "${msg} world"}},
    )
    result = pipeline.execute(steps)

    assert result.all_passed
    assert result.success_count == 2 and result.failed_count == 0
    # 下游步骤收到的参数已解析为 "hello world"
    assert registry.calls[1][1] == {"text": "hello world"}
    assert result.final_output == ""


def test_variable_resolution_nested_dict():
    """嵌套 dict 参数中的 ${var} 也应被递归解析。"""
    registry = FakeRegistry(results={"echo": "42"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "x"}, "output_var": "num"},
        {"id": "s2", "tool": "echo", "args": {"outer": {"inner": "${num}"}}},
    )
    pipeline.execute(steps)
    assert registry.calls[1][1] == {"outer": {"inner": "42"}}


def test_unknown_variable_keeps_placeholder():
    """未定义的变量应保留原占位符, 不抛异常。"""
    registry = FakeRegistry(results={"echo": "x"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps({"id": "s1", "tool": "echo", "args": {"text": "${missing}"}})
    result = pipeline.execute(steps)
    assert result.all_passed
    assert registry.calls[0][1] == {"text": "${missing}"}


def test_final_output_uses_result_var():
    """名为 result 的变量应作为 Pipeline 最终输出。"""
    registry = FakeRegistry(results={"echo": "最终答案"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps({"id": "s1", "tool": "echo", "args": {"text": "x"}, "output_var": "result"})
    result = pipeline.execute(steps)
    assert result.final_output == "最终答案"


# ------------------------------------------------------------------ 条件判断

def test_condition_skip_when_false():
    """条件不满足的步骤应被跳过, 且不调用工具。"""
    registry = FakeRegistry(results={"echo": "x"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "abc"}, "output_var": "v"},
        {"id": "s2", "tool": "echo", "args": {"text": "y"}, "condition": "v contains zzz"},
    )
    result = pipeline.execute(steps)
    assert result.skipped_count == 1
    assert result.steps[1].status == StepStatus.SKIPPED
    assert "条件不满足" in result.steps[1].error
    assert len(registry.calls) == 1


def test_condition_contains_true():
    """contains 条件满足时应执行步骤。"""
    registry = FakeRegistry(results={"echo": "abc"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "abc"}, "output_var": "v"},
        {"id": "s2", "tool": "echo", "args": {"text": "y"}, "condition": "v contains bc"},
    )
    result = pipeline.execute(steps)
    assert result.all_passed
    assert result.steps[1].status == StepStatus.SUCCESS


def test_condition_not_empty_and_empty():
    """not_empty / empty 条件判断。"""
    registry = FakeRegistry(results={"echo": "  "})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "x"}, "output_var": "blank"},
        {"id": "s2", "tool": "echo", "args": {"text": "y"}, "condition": "blank empty"},
        {"id": "s3", "tool": "echo", "args": {"text": "z"}, "condition": "blank not_empty"},
    )
    result = pipeline.execute(steps)
    assert result.steps[1].status == StepStatus.SUCCESS
    assert result.steps[2].status == StepStatus.SKIPPED


def test_condition_equals():
    """equals 条件判断。"""
    registry = FakeRegistry(results={"echo": "prod"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "x"}, "output_var": "env"},
        {"id": "s2", "tool": "echo", "args": {"text": "y"}, "condition": "env equals prod"},
        {"id": "s3", "tool": "echo", "args": {"text": "z"}, "condition": "env equals dev"},
    )
    result = pipeline.execute(steps)
    assert result.steps[1].status == StepStatus.SUCCESS
    assert result.steps[2].status == StepStatus.SKIPPED


def test_malformed_condition_defaults_true():
    """无法解析的条件表达式应默认执行。"""
    registry = FakeRegistry(results={"echo": "x"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps({"id": "s1", "tool": "echo", "args": {"text": "y"}, "condition": "weird"})
    result = pipeline.execute(steps)
    assert result.all_passed


# ------------------------------------------------------------------ 失败处理

def test_abort_stops_remaining_steps():
    """默认 ABORT 策略: 失败后后续步骤全部跳过。"""
    registry = FakeRegistry(results={"echo": "[错误] boom"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "a"}},
        {"id": "s2", "tool": "echo", "args": {"text": "b"}},
    )
    result = pipeline.execute(steps)
    assert result.failed_count == 1
    assert result.steps[0].status == StepStatus.FAILED
    assert result.steps[1].status == StepStatus.SKIPPED
    assert "已中止" in result.steps[1].error
    assert not result.all_passed


def test_skip_continues_after_failure():
    """SKIP 策略: 失败后继续执行后续步骤。"""
    registry = ScriptedRegistry(["[错误] boom", "ok"])
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "a"}, "on_failure": OnFailure.SKIP},
        {"id": "s2", "tool": "echo", "args": {"text": "b"}},
    )
    result = pipeline.execute(steps)
    assert result.failed_count == 1 and result.success_count == 1
    assert result.steps[1].status == StepStatus.SUCCESS


def test_retry_succeeds_on_second_attempt():
    """RETRY 策略: 首次失败后自动重试, 重试成功则记为成功。"""
    registry = ScriptedRegistry(["[错误] boom", "ok"])
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "a"}, "on_failure": OnFailure.RETRY},
    )
    result = pipeline.execute(steps)
    assert result.all_passed
    assert result.success_count == 1 and result.failed_count == 0
    assert len(registry.calls) == 2


def test_unknown_tool_fails_step():
    """未知工具应产生 FAILED 步骤且不抛异常。"""
    registry = FakeRegistry()
    pipeline = Pipeline(registry, _ctx())
    steps = _steps({"id": "s1", "tool": "nope"})
    result = pipeline.execute(steps)
    assert result.steps[0].status == StepStatus.FAILED
    assert "未知工具" in result.steps[0].error


def test_tool_exception_marked_failed():
    """工具抛异常应被捕获并标记为失败。"""
    class BoomRegistry(FakeRegistry):
        def __init__(self):
            super().__init__(results={"echo": "x"})

        def dispatch(self, name, arguments_json, ctx):
            raise RuntimeError("内部错误")

    pipeline = Pipeline(BoomRegistry(), _ctx())
    steps = _steps({"id": "s1", "tool": "echo"})
    result = pipeline.execute(steps)
    assert result.steps[0].status == StepStatus.FAILED
    assert "RuntimeError" in result.steps[0].error


# ------------------------------------------------------------------ 回调与报告

def test_step_callbacks_invoked():
    """on_step_start / on_step_end 回调应按步骤触发。"""
    registry = FakeRegistry(results={"echo": "x"})
    pipeline = Pipeline(registry, _ctx())
    starts, ends = [], []
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "a"}},
        {"id": "s2", "tool": "echo", "args": {"text": "b"}},
    )
    pipeline.execute(steps, on_step_start=lambda i, t: starts.append((i, t)),
                     on_step_end=lambda i, t, ok: ends.append((i, t, ok)))
    assert starts == [("s1", "echo"), ("s2", "echo")]
    assert ends == [("s1", "echo", True), ("s2", "echo", True)]


def test_to_report_contains_summary():
    """to_report 应包含通过/失败/跳过统计。"""
    registry = FakeRegistry(results={"echo": "[错误] boom"})
    pipeline = Pipeline(registry, _ctx())
    steps = _steps(
        {"id": "s1", "tool": "echo", "args": {"text": "a"}},
        {"id": "s2", "tool": "echo", "args": {"text": "b"}},
    )
    report = pipeline.execute(steps).to_report()
    assert "Pipeline 执行报告" in report
    assert "1 失败" in report and "1 跳过" in report


def test_pipeline_result_all_passed_empty():
    """空步骤列表的 all_passed 应为 True (空真)。"""
    assert PipelineResult().all_passed


# ------------------------------------------------------------------ pipeline_run 工具函数

def test_pipeline_run_with_real_kernel():
    """pipeline_run 应通过内核注册表执行多步链。"""
    from qingxiaotuan.config import Config
    from qingxiaotuan.core.kernel import Kernel
    from qingxiaotuan.tools import ToolRegistryPlugin, builtin_tool_plugins

    k = Kernel()
    k.provide("config", Config(), owner="test")
    k.register(ToolRegistryPlugin())
    for p in builtin_tool_plugins():
        if p.name == "tools.pipeline":
            k.register(p)
    k.activate_all()

    ctx = ToolContext(kernel=k, workspace=".")
    steps = [
        {"id": "s1", "tool": "pipeline_list_tools", "output_var": "tools"},
        {"id": "s2", "tool": "pipeline_list_tools", "condition": "tools not_empty"},
    ]
    out = pipeline_run(ctx, json.dumps(steps))
    assert "Pipeline 执行报告" in out
    assert "2 通过" in out


def test_pipeline_run_invalid_json():
    """非法 steps JSON 应返回错误提示。"""
    ctx = ToolContext(kernel=None, workspace=".")
    out = pipeline_run(ctx, "not-json")
    assert "不是合法 JSON" in out


def test_pipeline_run_empty_steps():
    """空步骤列表应返回错误提示。"""
    ctx = ToolContext(kernel=None, workspace=".")
    out = pipeline_run(ctx, "[]")
    assert "步骤列表为空" in out


def test_pipeline_list_tools_formats_schemas():
    """pipeline_list_tools 应列出工具名与参数 schema。"""
    from qingxiaotuan.config import Config
    from qingxiaotuan.core.kernel import Kernel
    from qingxiaotuan.tools import ToolRegistryPlugin, builtin_tool_plugins

    k = Kernel()
    k.provide("config", Config(), owner="test")
    k.register(ToolRegistryPlugin())
    for p in builtin_tool_plugins():
        if p.name == "tools.pipeline":
            k.register(p)
    k.activate_all()

    ctx = ToolContext(kernel=k, workspace=".")
    out = pipeline_list_tools(ctx)
    assert "pipeline_run" in out
    assert "steps_json" in out
