"""dispatch_tasks 工具 —— 让主 Agent 自己"分身"。

V2 增强: 支持 DAG 依赖调度 + 专业化角色分配 + 结果聚合。

当主 Agent 面对一组**相互独立**的子任务 (例如并行调研 N 个来源、对 N 个模块
分别做改动评估), 它可以调用本工具, 由 SubAgentPool 在后台并发派出隔离子 Agent,
等齐结果后把带来源标注的汇总一次性回传。

V2 新增:
- DAG 模式: 任务可以声明依赖关系 (depends_on), 调度器按拓扑排序分波执行
- 角色分配: 每个任务可以指定角色 (reviewer/tester/debugger/architect/...)
- 结果聚合: 自动去重 + 矛盾检测 + 质量评分
- 黑板通信: 任务间通过共享黑板传递中间结果
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from ..core.subagents import SubAgentPool, SubTask, make_tasks
from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext


def _run_dispatch(
    tasks: List[str],
    ctx: ToolContext,
    max_workers: int = 0,
    prompt_prefix: str = "",
    mode: str = "parallel",
    task_roles: Optional[List[str]] = None,
    task_dependencies: Optional[List[str]] = None,
) -> str:
    """由工具处理器调用: 并发执行一批子任务并返回汇总。

    tasks: 子任务提示词列表 (每项一段自然语言)。
    max_workers: 并发度 (0=用 agent.subagent_max_workers 默认)。
    prompt_prefix: 给每个子任务统一加的前缀 (例如"只做调研不要改文件: ")。
    mode: "parallel" (默认, 无依赖并行) | "dag" (依赖图调度) | "role" (角色化并行)。
    task_roles: 每个任务的角色 (与 tasks 等长, 仅 mode=role 时生效)。
    task_dependencies: 每个任务的依赖 (JSON 格式, 仅 mode=dag 时生效)。
    """
    if not tasks:
        return "[错误] dispatch_tasks 需要至少一个子任务 (tasks 列表非空)"
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        return "[错误] tasks 必须是字符串列表"

    kernel = ctx.kernel
    config = kernel.get("config")
    workspace = ctx.workspace

    def _confirm(_p: str) -> bool:
        return bool(ctx.yolo) or (ctx.confirm is not None and ctx.confirm(_p))

    pool = SubAgentPool(
        kernel=kernel,
        config=config,
        workspace=workspace,
        confirm=_confirm,
        exclude_tools=(),
        max_workers=max_workers or None,
        default_timeout=float(config.get("agent.subagent_timeout", 180)),
        isolation=config.get("agent.subagent_isolation", "process"),
    )

    # --- DAG 模式: 按依赖关系分波调度 ---
    if mode == "dag" and task_dependencies:
        return _dispatch_dag(
            tasks, task_dependencies, pool, prompt_prefix, ctx,
        )

    # --- Role 模式: 按角色分配系统提示 ---
    if mode == "role" and task_roles:
        return _dispatch_with_roles(
            tasks, task_roles, pool, prompt_prefix, kernel,
        )

    # --- 默认: 并行模式 ---
    subtasks = make_tasks(
        [f"{prompt_prefix}{t}" for t in tasks] if prompt_prefix else tasks
    )
    results = pool.dispatch(subtasks, stream=False)

    # V2: 用结果聚合器做去重+矛盾检测
    try:
        from ..core.result_aggregator import ResultAggregator
        factory = kernel.get("result_aggregator_factory")
        aggregator = factory() if factory else ResultAggregator()
        for r in results:
            aggregator.add_result(
                task_id=r.task_id, agent_id="subagent",
                content=r.output, elapsed=r.elapsed,
            )
        agg = aggregator.aggregate()
        base = SubAgentPool.aggregate(results, title="dispatch_tasks 子任务汇总")
        if agg.conflicts:
            base += "\n\n## ⚠️ 结果矛盾检测\n"
            for c in agg.conflicts:
                base += f"- {c['description']}\n"
        return base
    except Exception:  # noqa: BLE001
        return SubAgentPool.aggregate(results, title="dispatch_tasks 子任务汇总")


def _dispatch_dag(
    tasks: List[str],
    task_dependencies: Union[str, List[Any]],
    pool: "SubAgentPool",
    prompt_prefix: str,
    ctx: ToolContext,
) -> str:
    """DAG 模式: 按依赖关系分波调度。"""
    from ..core.task_dag import TaskDAG
    import json

    # 解析依赖声明: [{"id": "T1", "depends_on": "T2"}, ...]
    try:
        deps = json.loads(task_dependencies) if isinstance(task_dependencies, str) else task_dependencies
    except (json.JSONDecodeError, TypeError):
        deps = []

    dag = TaskDAG()
    for i, task_text in enumerate(tasks):
        tid = f"T{i+1}"
        dep_ids = []
        for d in deps:
            if isinstance(d, dict) and d.get("id") == tid:
                dep_ids = [x.strip() for x in str(d.get("depends_on", "")).split(",") if x.strip()]
        dag.add_node(tid, f"子任务{i+1}", f"{prompt_prefix}{task_text}" if prompt_prefix else task_text)
        dag.add_dependencies(tid, dep_ids)

    # 打破循环依赖
    cycles = dag.detect_cycles()
    if cycles:
        dag.break_cycles()

    # 分波执行
    all_results = []
    max_concurrent = int(pool.max_workers)
    while True:
        wave = dag.get_wave(max_concurrent)
        if not wave:
            break
        from ..core.subagents import SubTask
        subtasks = [
            SubTask(task_id=n.task_id, prompt=n.prompt)
            for n in wave
        ]
        for node in wave:
            dag.mark_running(node.task_id)
        results = pool.dispatch(subtasks, stream=False)
        for r in results:
            dag.mark_completed(r.task_id, r.output)
            all_results.append(r)

    return SubAgentPool.aggregate(all_results, title="dispatch_tasks DAG 汇总")


def _dispatch_with_roles(
    tasks: List[str],
    task_roles: List[str],
    pool: "SubAgentPool",
    prompt_prefix: str,
    kernel: "Kernel",
) -> str:
    """Role 模式: 按角色分配系统提示。"""
    from ..core.subagents import SubTask

    # 获取角色注册表
    role_registry = kernel.get("role_registry")
    get_role = role_registry.get("get") if role_registry else None

    subtasks = []
    for i, task_text in enumerate(tasks):
        role_id = task_roles[i] if i < len(task_roles) else "implementer"
        system_extra = ""
        if get_role:
            role = get_role(role_id)
            if role:
                system_extra = role.to_system_extra()
        prompt = f"{prompt_prefix}{task_text}" if prompt_prefix else task_text
        subtasks.append(SubTask(
            task_id=f"T{i+1}",
            prompt=prompt,
            meta={"system_extra": system_extra, "role": role_id},
        ))

    results = pool.dispatch(subtasks, stream=False)
    return SubAgentPool.aggregate(results, title="dispatch_tasks 角色化汇总")


def build_tool() -> Tool:
    return Tool(
        name="dispatch_tasks",
        description=(
            "V2: 并发派出多个隔离子Agent处理任务, 支持三种模式:\n"
            "1) parallel (默认): 无依赖并行, 结果自动去重+矛盾检测;\n"
            "2) dag: 按依赖关系分波调度 (task_dependencies 指定依赖);\n"
            "3) role: 按专业化角色分配 (reviewer/tester/debugger/architect等)。\n"
            "子Agent与主Agent共享工具, 但各自上下文隔离。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "子任务提示词列表。",
                },
                "max_workers": {
                    "type": "integer",
                    "description": "并发子Agent数量上限 (0=默认)。",
                    "default": 0,
                },
                "prompt_prefix": {
                    "type": "string",
                    "description": "给每个子任务统一加的前缀。",
                    "default": "",
                },
                "mode": {
                    "type": "string",
                    "enum": ["parallel", "dag", "role"],
                    "description": "调度模式: parallel=并行, dag=依赖图, role=角色化。",
                    "default": "parallel",
                },
                "task_roles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "每个任务的角色ID (mode=role时生效)。可选: reviewer/tester/debugger/architect/security_auditor/documenter/implementer。",
                },
                "task_dependencies": {
                    "type": "string",
                    "description": "任务依赖JSON (mode=dag时生效), 格式: [{\"id\":\"T1\",\"depends_on\":\"T2\"}]。",
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
                      prompt_prefix: Any = "", mode: Any = "parallel",
                      task_roles: Any = None, task_dependencies: Any = None,
                      **_kwargs) -> str:
    tasks = tasks or []
    return _run_dispatch(
        tasks,
        ctx,
        max_workers=int(max_workers or 0),
        prompt_prefix=str(prompt_prefix or ""),
        mode=str(mode or "parallel"),
        task_roles=task_roles if isinstance(task_roles, list) else None,
        task_dependencies=task_dependencies,
    )
