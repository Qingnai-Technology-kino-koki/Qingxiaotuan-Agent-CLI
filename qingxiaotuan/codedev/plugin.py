"""codedev.plugin —— 把代码开发子系统接入内核（服务 + 工具）。

注册内容：
    - 内核服务 ``codedev``：CodeDevEngine 实例, 供子代理/其它模块复用。
    - 四个工具（Claude Code 风格, 模型只要会「调工具」即可使用）：
        * codedev_retrieve  只读  —— 任务 → 刚好相关的代码上下文（确定性, 零 token）
        * codedev_verify    只读  —— 跑 build/test/lint, 返回 file:line 诊断 + 修复提示
        * codedev_spec      只读  —— 任务 → 可并行子任务计划（markdown）
        * codedev_develop  读写  —— 编排检索+分解+验证, 默认返回上下文/计划/诊断（便宜）;
                                     spawn=True 时并发子代理（受成本闸门约束）

设计红线：本插件**不引入任何新的模型循环**。codedev_develop 默认只做确定性步骤,
真正的子代理执行复用既有 Agent Loop（经 arch.Orchestrator + kernel_executor）。
"""

from __future__ import annotations

from typing import Any

from ..core.kernel import Plugin
from ..tools.base import Tool, ToolContext
from .engine import CodeDevEngine


def _make_engine(ctx: ToolContext) -> CodeDevEngine:
    kernel = getattr(ctx, "kernel", None)
    workspace = getattr(ctx, "workspace", None)
    return CodeDevEngine(kernel=kernel, workspace=workspace)


# --------------------------------------------------------------------------- #
# 工具处理器
# --------------------------------------------------------------------------- #
def codedev_retrieve(ctx: ToolContext, task: str, top_k: int = 8) -> str:
    """检索与任务最相关的代码上下文（文件+符号片段+调用邻居）。只读、零 token。"""
    engine = _make_engine(ctx)
    res = engine.forge_context(task, top_k=top_k)
    header = (f"[codedev.retrieve] 扫描 {res.files_scanned} 文件 / 索引 {res.symbols_indexed} 符号, "
              f"命中 {len(res.hits)} 个相关符号, 耗时 {res.elapsed_ms:.0f}ms\n")
    return header + res.pack()


def codedev_verify(ctx: ToolContext, extra_command: str = "") -> str:
    """在 workspace 运行验证闸门（build/test/lint）, 返回结构化诊断与修复提示。只读观察。"""
    engine = _make_engine(ctx)
    extra = None
    if extra_command.strip():
        extra = [extra_command.strip().split()]
    report = engine.verify(extra_commands=extra)
    return report.summary()


def codedev_spec(ctx: ToolContext, task: str, max_subtasks: int = 5, top_k: int = 8) -> str:
    """把任务分解为可并行、各自带上下文的子任务计划（markdown）。只读、确定性。"""
    engine = _make_engine(ctx)
    spec = engine.plan(task, top_k=top_k, max_subtasks=max_subtasks)
    return spec.plan_md


def codedev_develop(
    ctx: ToolContext,
    task: str,
    decompose: bool = True,
    verify: bool = True,
    spawn: bool = False,
    max_subtasks: int = 5,
    top_k: int = 8,
) -> str:
    """编排「检索→分解→验证」开发流程。

    - 默认（spawn=False）：仅返回上下文/计划/诊断（确定性, 便宜, 不跑子代理）。
    - spawn=True：尝试并发子代理实现（复用既有 Agent Loop, 受成本闸门约束）。
    - 本工具本身不引入新循环; 子代理执行走既有编排。
    """
    engine = _make_engine(ctx)
    executor = None
    if spawn:
        try:
            from ..arch.orchestration import kernel_executor
            if engine.kernel is not None:
                executor = kernel_executor(engine.kernel)
        except Exception:  # noqa: BLE001
            executor = None
    result = engine.develop(
        task, decompose=decompose, verify=verify,
        max_subtasks=max_subtasks, top_k=top_k, executor=executor,
    )
    lines = [f"[codedev.develop] 任务: {result.task}",
             f"成本倍数 ≈ {result.cost_factor:.1f}x（预算 {result.cost_budget_pct:.0f}%, "
             f"{'✅ 内' if result.within_budget else '⚠️ 超预算回退'}）",
             "", "## 检索上下文", result.context_pack[:2000]]
    if result.spec:
        lines += ["", "## 计划", result.spec.plan_md]
    if result.diagnostics:
        lines += ["", "## 验证诊断", f"共 {len(result.diagnostics)} 条, "
                  f"{sum(1 for d in result.diagnostics if getattr(d, 'severity', '') == 'error')} error"]
    if result.subagent_summaries:
        lines += ["", "## 子代理", *result.subagent_summaries]
    lines += ["", "## 技能蒸馏", result.skill]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 插件
# --------------------------------------------------------------------------- #
class CodeDevPlugin(Plugin):
    """注册 codedev 服务与四个工具。"""
    name = "codedev"

    def activate(self, kernel: Any) -> None:
        workspace = kernel.get("workspace") if hasattr(kernel, "get") else None
        engine = CodeDevEngine(kernel=kernel, workspace=workspace)
        try:
            kernel.register("codedev", engine)
        except Exception:  # noqa: BLE001
            if hasattr(kernel, "provide"):
                kernel.provide("codedev", engine)

        reg = kernel.require("tool_registry")
        reg.register(Tool(
            name="codedev_retrieve", group="codedev", read_only=True,
            description="检索与任务最相关的代码上下文（文件+符号片段+调用邻居）。"
                        "弱模型应优先调用它, 而不是盲目 grep。返回可直接注入 prompt 的上下文包。",
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "自然语言任务描述（支持中文）"},
                    "top_k": {"type": "integer", "description": "返回的相关符号数量, 默认 8", "default": 8},
                },
                "required": ["task"],
            },
            handler=codedev_retrieve,
        ))
        reg.register(Tool(
            name="codedev_verify", group="codedev", read_only=True,
            description="在 workspace 运行验证闸门（build/test/lint）, 返回精确到 file:line 的诊断"
                        "与常见错误修复提示。用于写完代码后自检。",
            parameters={
                "type": "object",
                "properties": {
                    "extra_command": {"type": "string", "description": "额外验证命令, 如 'pytest tests/test_x.py'", "default": ""},
                },
                "required": [],
            },
            handler=codedev_verify,
        ))
        reg.register(Tool(
            name="codedev_spec", group="codedev", read_only=True,
            description="把任务分解为可并行的子任务计划（markdown）。每个子任务带独立上下文, "
                        "便于后续交给子代理并发实现。",
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "任务描述"},
                    "max_subtasks": {"type": "integer", "description": "最大子任务数, 默认 5", "default": 5},
                    "top_k": {"type": "integer", "description": "检索上下文深度, 默认 8", "default": 8},
                },
                "required": ["task"],
            },
            handler=codedev_spec,
        ))
        reg.register(Tool(
            name="codedev_develop", group="codedev", read_only=False, long_running=True,
            description="编排「检索→分解→验证」的开发流程。默认仅返回上下文/计划/诊断（便宜）;"
                        "spawn=true 时并发子代理实现（复用既有 Agent Loop, 受成本闸门约束）。",
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "开发任务"},
                    "decompose": {"type": "boolean", "description": "是否分解为子任务, 默认 true", "default": True},
                    "verify": {"type": "boolean", "description": "是否运行验证闸门, 默认 true", "default": True},
                    "spawn": {"type": "boolean", "description": "是否并发子代理实现, 默认 false（仅规划+检索+验证）", "default": False},
                    "max_subtasks": {"type": "integer", "description": "最大子任务数", "default": 5},
                    "top_k": {"type": "integer", "description": "检索深度", "default": 8},
                },
                "required": ["task"],
            },
            handler=codedev_develop,
        ))
