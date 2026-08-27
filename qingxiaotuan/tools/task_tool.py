"""task / background_status 工具 —— 对标 Claude Code 的 Task 工具 (单任务委派)。

主 Agent 通过 task 工具把**单个**子任务委派给指定类型的隔离子代理:
- subagent_type 决定子代理的角色指令 (追加进系统提示) 与能力边界 (只读与否);
- 前台模式: 等待子代理完成, 结果文本直接回灌主上下文;
- 后台模式 (run_in_background=true): 立即返回 job_id, 主 Agent 之后用
  background_status 工具查询进展/结果, 等待期间可继续处理其他工作。

只读类型 (explore / plan) 通过 exclude_tools=readonly_tool_names() 在工具层
物理剥夺一切修改类手段, 且 task 自身也在被排除之列 —— 杜绝递归派生孙代理。
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Optional, TYPE_CHECKING

from ..core.agent_types import (
    get_agent_type,
    load_custom_types,
    readonly_tool_names,
    render_types_for_prompt,
    type_names,
)
from ..core.kernel import Kernel, Plugin
from ..logging_conf import log
from ..core.subagents import SubAgentPool, SubTask
from .base import Tool, ToolContext, string_prop

if TYPE_CHECKING:  # 仅类型标注; 运行时延迟导入以打破 tools -> core.background -> core.agent 循环
    from ..core.background import BackgroundRunner


# ------------------------------------------------------------------ 公共小件

def _inherit_confirm(ctx: ToolContext) -> Callable[[str], bool]:
    """子代理的确认策略: 与 dispatch_tasks 同一套语义 (YOLO 下自动通过)。"""
    return lambda _p: bool(ctx.yolo) or (ctx.confirm is not None and ctx.confirm(_p))


def _make_pool(ctx: ToolContext) -> SubAgentPool:
    """为单任务委派构造 SubAgentPool (隔离级别/超时与 dispatch 工具同一配置源)。"""
    config = ctx.kernel.get("config")
    return SubAgentPool(
        kernel=ctx.kernel,
        config=config,
        workspace=ctx.workspace,
        confirm=_inherit_confirm(ctx),
        max_workers=1,  # 单任务委派无需并发度
        default_timeout=float(config.get("agent.subagent_timeout", 180)),
        isolation=config.get("agent.subagent_isolation", "process"),
    )


def _get_runner(ctx: ToolContext) -> "BackgroundRunner":
    """懒创建并缓存在 ctx 上的后台 Runner —— submit 与 status 必须共用同一实例。"""
    from ..core.background import BackgroundRunner  # 延迟导入, 避免循环依赖

    runner: Optional["BackgroundRunner"] = getattr(ctx, "background_runner", None)
    if runner is None:
        runner = BackgroundRunner(ctx.kernel, ctx.kernel.get("config"), ctx.workspace)
        ctx.background_runner = runner
    return runner


# ------------------------------------------------------------------ task 工具

def _task_handler(ctx: ToolContext, description: Any = "", prompt: Any = "",
                  subagent_type: Any = "general-purpose",
                  run_in_background: Any = False, **_kwargs) -> str:
    prompt = str(prompt or "")
    if not prompt.strip():
        return "[错误] task 需要非空的 prompt 参数"
    atype = get_agent_type(str(subagent_type or ""))
    if atype is None:
        return (f"[错误] 未知的 subagent_type: {subagent_type}。"
                f"可用类型:\n{render_types_for_prompt()}")

    # 能力边界: 只读类型排除注册表里所有非只读工具 (含 task 自身, 防递归派生)
    exclude: tuple = ()
    meta: dict = {}
    if atype.read_only:
        registry = ctx.kernel.require("tool_registry")
        exclude = tuple(readonly_tool_names(registry))
    if atype.system_extra:
        meta["system_extra"] = atype.system_extra

    label = str(description or "").strip() or prompt[:40]
    task_id = "t-" + uuid.uuid4().hex[:6]

    if bool(run_in_background):
        try:
            runner = _get_runner(ctx)
            job = runner.submit(
                prompt, confirm=_inherit_confirm(ctx),
                exclude_tools=exclude, system_extra=atype.system_extra,
            )
        except RuntimeError as exc:
            return f"[错误] 后台委派失败: {exc}"
        return (
            f"后台子代理已启动 ({atype.name}, 任务: {label})。\n"
            f"job_id: {job.job_id}\n"
            f'请稍后用 background_status 工具 (job_id="{job.job_id}") 查询结果;'
            f"等待期间可继续处理其他工作。"
        )

    pool = _make_pool(ctx)
    result = pool.dispatch([SubTask(task_id=task_id, prompt=prompt,
                                    exclude_tools=exclude, meta=meta)])[0]
    if not result.ok:
        return (f"[{atype.name}] 子任务 \"{label}\" 执行失败\n\n"
                f"{result.error or result.output or '(无输出)'}")
    return result.to_block()


def build_task_tool() -> Tool:
    return Tool(
        name="task",
        description=(
            "把一个独立子任务委派给指定类型的隔离子代理执行 (适合需要大量检索/探索、\n"
            "或与主线工作互不依赖的子任务)。可用 subagent_type:\n"
            f"{render_types_for_prompt()}\n"
            "前台执行会阻塞至完成并把结果回灌上下文; 设 run_in_background=true 则立即\n"
            "返回 job_id, 之后用 background_status 查询结果。只读类型的子代理无法修改任何文件。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "description": string_prop("任务的 3-5 词短描述 (供展示进度用)"),
                "prompt": {"type": "string",
                           "description": "交给子代理的完整任务说明 (自包含, 它看不到你们的对话历史)"},
                "subagent_type": {
                    "type": "string",
                    "enum": type_names(),
                    "description": f"子代理类型, 默认 general-purpose:\n{render_types_for_prompt()}",
                },
                "run_in_background": {"type": "boolean", "default": False,
                                      "description": "true=后台运行, 立即返回 job_id"},
            },
            "required": ["prompt"],
        },
        handler=_task_handler,
        dangerous=False,
        group="agent",
    )


# ------------------------------------------------------------------ background_status 工具

def _status_handler(ctx: ToolContext, job_id: Any = "", tail: Any = 10, **_kwargs) -> str:
    runner: Optional[Any] = getattr(ctx, "background_runner", None)
    if runner is None:
        return "当前会话还没有启动过任何后台子代理任务。"
    jid = str(job_id or "").strip()
    if not jid:
        jobs = runner.list_jobs()
        if not jobs:
            return "没有后台任务。"
        lines = ["后台任务列表:"]
        for j in jobs:
            d = j.to_dict()
            lines.append(f"- {d['job_id']} [{d['status']}] 轮次 {d['turns']} · "
                         f"{d['elapsed']}s · {(d.get('task') or '')[:50]}")
        return "\n".join(lines)
    job = runner.get(jid)
    if job is None:
        return f"[错误] 未找到后台任务: {jid} (可用不带 job_id 的调用列出全部任务)"
    parts = [f"[{job.status}] {job.task[:100]}"]
    if job.status == "running":
        tl = job.tail(int(tail))
        if tl:
            parts.append("最近动态:\n" + "\n".join(tl))
        parts.append("仍在运行中, 请稍后再次查询。")
    else:
        if job.result:
            parts.append(f"结果:\n{job.result}")
        elif job.error:
            parts.append(f"错误: {job.error}")
        else:
            parts.append("(无输出)")
    return "\n".join(parts)


def build_background_status_tool() -> Tool:
    return Tool(
        name="background_status",
        description=(
            "查询后台子代理任务的状态与结果。不传 job_id 时列出全部后台任务;"
            "传入 job_id 时返回该任务的详细进展或最终结果 (配合 task 工具的"
            "run_in_background 使用)。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "job_id": string_prop("要查询的后台任务 ID; 留空则列出全部"),
                "tail": {"type": "integer", "default": 10,
                         "description": "运行中任务展示的最近动态条数"},
            },
        },
        handler=_status_handler,
        dangerous=False,
        group="agent",
        read_only=True,
    )


class TaskToolPlugin(Plugin):
    """类型化子代理委派插件: 注册 task / background_status 两个工具。"""

    name = "tools.task"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        # 先加载用户自定义 subagent 类型 (<QXT_HOME>/agents/*.md),
        # 让 task 工具的 description/enum 固化时已包含它们 (重启会话生效)。
        config = kernel.get("config")
        if config is not None:
            try:
                load_custom_types(config.home)
            except Exception as exc:  # noqa: BLE001
                log.warning("加载自定义 subagent 类型失败: %s", exc)
        registry.register(build_task_tool())
        registry.register(build_background_status_tool())
