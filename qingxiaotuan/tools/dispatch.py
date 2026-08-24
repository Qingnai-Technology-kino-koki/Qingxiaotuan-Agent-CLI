"""dispatch_tasks 工具 —— 让主 Agent 自己"分身"。

当主 Agent 面对一组**相互独立**的子任务 (例如并行调研 N 个来源、对 N 个模块
分别做改动评估), 它可以调用本工具, 由 SubAgentPool 在后台并发派出隔离子 Agent,
等齐结果后把带来源标注的汇总一次性回传。

子 Agent 与主 Agent **共享同一个内核工具注册表** —— 换言之, 它们天生拥有相同的
"手" (shell / web_fetch / filesystem / code …), 但各自上下文隔离, 互不污染。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..core.subagents import SubAgentPool, SubTask, make_tasks
from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext


def _run_dispatch(
    tasks: List[str],
    ctx: ToolContext,
    max_workers: int = 0,
    prompt_prefix: str = "",
) -> str:
    """由工具处理器调用: 并发执行一批子任务并返回汇总。

    tasks: 子任务提示词列表 (每项一段自然语言)。
    max_workers: 并发度 (0=用 agent.subagent_max_workers 默认)。
    prompt_prefix: 给每个子任务统一加的前缀 (例如"只做调研不要改文件: ")。
    """
    if not tasks:
        return "[错误] dispatch_tasks 需要至少一个子任务 (tasks 列表非空)"
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        return "[错误] tasks 必须是字符串列表"

    kernel = ctx.kernel
    config = kernel.get("config")
    workspace = ctx.workspace

    def _confirm(_p: str) -> bool:
        # 子 Agent 在共享工具上执行: 继承主上下文的确认策略 (yolo 下自动通过)
        return bool(ctx.yolo) or (ctx.confirm is not None and ctx.confirm(_p))

    pool = SubAgentPool(
        kernel=kernel,
        config=config,
        workspace=workspace,
        confirm=_confirm,
        exclude_tools=(),
        max_workers=max_workers or None,
        default_timeout=float(config.get("agent.subagent_timeout", 180)),
        # 隔离级别: process=进程级沙箱(生产默认); thread=线程软隔离(测试/受限环境)
        isolation=config.get("agent.subagent_isolation", "process"),
    )
    subtasks = make_tasks(
        [f"{prompt_prefix}{t}" for t in tasks] if prompt_prefix else tasks
    )
    results = pool.dispatch(subtasks, stream=False)
    return SubAgentPool.aggregate(results, title="dispatch_tasks 子任务汇总")


def build_tool() -> Tool:
    return Tool(
        name="dispatch_tasks",
        description=(
            "并发派出多个隔离的子 Agent 去并行处理一组**相互独立**的子任务 "
            "(例如同时调研多个来源、分别评估多个模块), 等齐结果后汇总回传。"
            "子 Agent 与主 Agent 共享同样的工具 (联网/读文件/跑命令), 但各自上下文隔离。"
            "只在子任务之间确实无依赖时使用; 若后续步骤依赖前一步产出, 请勿使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "子任务提示词列表, 每项是一段独立可执行的任务描述。",
                },
                "max_workers": {
                    "type": "integer",
                    "description": "并发子 Agent 数量上限 (0=用默认配置, 通常 4)。",
                    "default": 0,
                },
                "prompt_prefix": {
                    "type": "string",
                    "description": "给每个子任务统一加的前缀, 例如 '只做调研, 不要修改任何文件: '。",
                    "default": "",
                },
            },
            "required": ["tasks"],
        },
        handler=_dispatch_handler,
        dangerous=False,
        group="agent",
    )


class DispatchPlugin(Plugin):
    name = "tools.dispatch"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(build_tool())


def _dispatch_handler(ctx: ToolContext, tasks: Any = None, max_workers: Any = 0,
                      prompt_prefix: Any = "", **_kwargs) -> str:
    tasks = tasks or []
    return _run_dispatch(
        tasks,
        ctx,
        max_workers=int(max_workers or 0),
        prompt_prefix=str(prompt_prefix or ""),
    )
