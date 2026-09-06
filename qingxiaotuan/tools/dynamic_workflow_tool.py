"""Dynamic Workflows 工具 —— 并行后台代理编排的模型入口。

- ``dynamic_workflow``: 创建并立即签发一个并行工作流 (异步执行, 返回 wf_id)。
- ``workflow_status``: 查询/管理已有工作流 (list/status/result/add_step/cancel/retry_failed)。

编排层见 core/dynamic_workflow.py (DynamicWorkflowEngine)。工作流跑在
SubAgentPool 之上: 每步内并行, 步骤间顺序, 支持运行中动态加步骤、失败重试、
结果聚合、进程重启后可查询的持久化状态。
"""

from __future__ import annotations

from typing import Any, Optional

from .base import Tool, ToolResult, ToolContext


def _engine(ctx: ToolContext):
    """取当前 run 的共享引擎并注入运行时资源。"""
    from ..config import home_dir
    from ..core.dynamic_workflow import get_engine

    home = home_dir()
    eng = get_engine(home)
    kernel = getattr(ctx, "kernel", None)
    if kernel is not None:
        config = kernel.get("config")
        eng.configure(
            kernel=kernel,
            config=config,
            workspace=getattr(ctx, "workspace", "") or "",
            main_agent=getattr(ctx, "agent", None),
            max_parallel=int((config.get("agent.subagent_max_workers", 5)
                              if config is not None else 5)),
        )
    return eng


# ---------------------------------------------------------------- dynamic_workflow 工具

def _start_workflow(ctx: ToolContext, name: Optional[str] = "",
                    steps: Any = None, parallel: Any = True,
                    note: Optional[str] = "", **kwargs) -> str:
    from ..core.dynamic_workflow import parse_steps

    eng = _engine(ctx)
    parsed = parse_steps(steps)
    if not parsed or not any(st for st in parsed):
        return "[错误] 需提供 steps: 二维数组, 每步是 [ {title, prompt, role?}, ... ]"
    if not name:
        name = "动态工作流"
    try:
        wf_id = eng.create(name, parsed, parallel=bool(parallel), note=note or "")
    except Exception as exc:  # noqa: BLE001
        return f"[错误] 创建工作流失败: {exc}"
    total = sum(len(st) for st in parsed)
    return (
        f"已启动动态工作流 [{name}] -> {wf_id}\n"
        f"  步骤: {len(parsed)} 步, 共 {total} 个任务, 并行={bool(parallel)}\n"
        "  用 workflow_status action=status wf_id=<id> 查进度;\n"
        "  action=result 拿聚合报告; action=add_step 可动态追加步骤。"
    )


def _status_workflow(ctx: ToolContext, action: str = "status", wf_id: str = "",
                     steps_json: Any = None, name: Optional[str] = None,
                     title: str = "", prompt: str = "", role: str = "general-purpose",
                     **kwargs) -> str:
    from ..core.dynamic_workflow import parse_steps
    eng = _engine(ctx)
    action = (action or "status").lower()

    if action == "list":
        items = eng.list()
        if not items:
            return "(暂无工作流)"
        lines = [f"工作流共 {len(items)} 个:"]
        for it in items[:20]:
            lines.append(
                f"  {it['workflow_id']:<16} {it['status']:<9} 步骤={it['n_steps']}  {it['name']}")
        return "\n".join(lines)

    if not wf_id:
        if action == "help":
            return "workflow_status: action=list|status|result|add_step|cancel|retry_failed, 均需 wf_id"
        return "[错误] 除 list 外需提供 wf_id"

    if action == "status":
        try:
            st = eng.status(wf_id)
        except KeyError:
            return f"[错误] 找不到工作流 {wf_id}"
        lines = [f"工作流 {st['name']} [{st['workflow_id']}] · 状态={st['status']}"
                 f" · {st['n_steps']} 步 · 并行={st['parallel']}"]
        for s in st["steps"]:
            lines.append(f"  步骤 {s['step'] + 1}: {s['status']} ({s['done_tasks']}/{s['n_tasks']})")
        return "\n".join(lines)

    if action == "result":
        try:
            r = eng.result(wf_id)
        except KeyError:
            return f"[错误] 找不到工作流 {wf_id}"
        head = (f"工作流 {r['name']} [{wf_id}] · 状态={r['status']} · "
                f"成功 {r['ok_tasks']}/{r['total_done']}")
        return f"{head}\n\n{r['summary']}"

    if action == "add_step":
        tasks = parse_steps(steps_json)
        flat: Any = []
        for st in tasks:
            flat.extend(st)
        if not flat:
            return "[错误] add_step 需提供 tasks: [ {title, prompt, role?}, ... ]"
        try:
            idx = eng.add_step(wf_id, flat)
        except KeyError:
            return f"[错误] 找不到工作流 {wf_id}"
        except RuntimeError as exc:
            return f"[错误] {exc}"
        return f"已动态追加步骤 #{idx + 1} ({len(flat)} 个任务) 到 {wf_id}"

    if action == "cancel":
        try:
            ok = eng.cancel(wf_id)
        except KeyError:
            return f"[错误] 找不到工作流 {wf_id}"
        return f"已请求取消 {wf_id}" if ok else f"工作流 {wf_id} 已结束或正在取消"

    if action == "retry_failed":
        try:
            ok = eng.retry_failed(wf_id)
        except KeyError:
            return f"[错误] 找不到工作流 {wf_id}"
        return f"已重置失败任务并重新编排 {wf_id}" if ok else f"{wf_id} 无失败任务可重试"

    return f"[错误] 未知 action={action} (可用 list/status/result/add_step/cancel/retry_failed)"


def build_dynamic_workflow_tool() -> Tool:
    return Tool(
        name="dynamic_workflow",
        description=(
            "启动一个动态并行工作流: 一批独立子代理并行编排。"
            "steps 是二维数组: 每步一批任务, 步间顺序、步内并行。"
            "每任务是 {title, prompt, role?}。立即返回 wf_id (后台异步执行)。"
            "配合 workflow_status 查进度/取结果/动态加步骤。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工作流名称 (展示用)"},
                "steps": {
                    "type": "array",
                    "description": "二维数组: [[{title,prompt,role?}, ...], ...], 每内层是一道并行步骤",
                    "items": {"type": "array", "items": {"type": "object"}},
                },
                "parallel": {"type": "boolean", "default": True,
                             "description": "步内是否并行 (true=并行, false=步内串行)"},
                "note": {"type": "string", "description": "备注"},
            },
            "required": ["steps"],
        },
        handler=_start_workflow,
        group="agent",
    )


def build_workflow_status_tool() -> Tool:
    return Tool(
        name="workflow_status",
        description=(
            "管理动态工作流: 查询状态/取聚合结果/动态加步骤/取消/重试失败。"
            "action=list 不带 wf_id; 其余都需 wf_id。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": [
                    "list", "status", "result", "add_step", "cancel", "retry_failed"]},
                "wf_id": {"type": "string", "description": "工作流 id"},
                "steps_json": {
                    "type": "array",
                    "description": "add_step 用的任务清单 [{title, prompt, role?}, ...]",
                    "items": {"type": "object"},
                },
                "name": {"type": "string"},
                "title": {"type": "string", "description": "add_step 单个任务标题"},
                "prompt": {"type": "string", "description": "add_step 单个任务说明"},
            },
            "required": [],
        },
        handler=_status_workflow,
        group="agent",
    )


class DynamicWorkflowPlugin:
    name = "tools.dynamic_workflow"
    provides = ["dynamic_workflow_engine"]
    requires = ["tool_registry"]

    def activate(self, kernel) -> None:
        from ..config import home_dir
        from ..core.dynamic_workflow import get_engine

        # 提供共享引擎服务 (运行时资源由调用方按需 configure 注入)
        eng = get_engine(home_dir())
        config = kernel.get("config")
        if config is not None:
            eng.configure(
                kernel=kernel,
                config=config,
                workspace=str(getattr(config, "cwd", None) or getattr(config, "home", "") or ""),
                main_agent=None,
            )
        kernel.provide("dynamic_workflow_engine", eng, owner=self.name)

        registry = kernel.require("tool_registry")
        registry.register(build_dynamic_workflow_tool())
        registry.register(build_workflow_status_tool())