"""会话级交互工具 —— 对标 Claude Code 的 TodoWrite/TodoRead、AskUserQuestion 与 Plan Mode 工具化。

- todo_write / todo_read: 任务清单存于 ToolContext (会话内共享), 供主 Agent 规划并
  跟踪多步任务的进度 (status: pending / in_progress / completed);
- ask_user: 需要用户拍板时主动提问 (带选项); 非交互环境 (管道/headless) 安全降级为
  提示文本, 由模型自行决策并向用户说明;
- enter_plan_mode / exit_plan_mode: 把 Plan 模式切换从「用户手敲 /plan」开放给模型,
  exit 时强制附上实施计划文本回灌上下文, 形成「规划 → 确认 → 执行」闭环。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop

_TODO_STATUSES = ("pending", "in_progress", "completed")


def _set_plan_mode(ctx: ToolContext, on: bool) -> None:
    """同步三处 Plan 模式状态: ToolContext (分发拦截依据)、宿主 Agent (UI 展示依据)、内核事件。"""
    ctx.plan_mode = on
    agent = getattr(ctx, "agent", None)
    if agent is not None:
        try:
            agent.plan_mode = on
        except Exception:  # noqa: BLE001
            pass
    try:
        ctx.kernel.emit("plan_mode.changed", {"enabled": on})
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ todo_write / todo_read

def _todo_write_handler(ctx: ToolContext, todos: Any = None, **_kwargs) -> str:
    if not isinstance(todos, list) or not todos:
        return "[错误] todo_write 需要非空的 todos 数组 (全量覆盖语义)"
    cleaned: List[Dict[str, Any]] = []
    for i, item in enumerate(todos, 1):
        if not isinstance(item, dict):
            return f"[错误] 第 {i} 项不是对象: {item!r}"
        content = str(item.get("content", "")).strip()
        status = str(item.get("status", "pending")).strip().lower()
        if not content:
            return f"[错误] 第 {i} 项缺少 content"
        if status not in _TODO_STATUSES:
            return (f"[错误] 第 {i} 项 status 非法: {status} "
                    f"(允许: {'/'.join(_TODO_STATUSES)})")
        cleaned.append({"content": content, "status": status})
    if sum(1 for t in cleaned if t["status"] == "in_progress") > 1:
        return "[错误] 同时只能有一项 in_progress (请先完成当前项)"
    ctx.todos = cleaned
    try:
        ctx.kernel.emit("todos.updated", {"todos": list(cleaned)})
    except Exception:  # noqa: BLE001
        pass
    done = sum(1 for t in cleaned if t["status"] == "completed")
    return f"任务清单已更新 ({done}/{len(cleaned)} 完成)。"


def _todo_read_handler(ctx: ToolContext, **_kwargs) -> str:
    todos: List[Dict[str, Any]] = getattr(ctx, "todos", None) or []
    if not todos:
        return "(任务清单为空)"
    icons = {"pending": "☐", "in_progress": "◔", "completed": "☑"}
    lines = [f"{icons.get(t['status'], '☐')} [{t['status']}] {t['content']}" for t in todos]
    done = sum(1 for t in todos if t["status"] == "completed")
    lines.append(f"进度: {done}/{len(todos)}")
    return "\n".join(lines)


def build_todo_write_tool() -> Tool:
    return Tool(
        name="todo_write",
        description=(
            "写入/更新会话任务清单 (全量覆盖)。开始一个多步骤任务前先用它列出步骤,\n"
            "每完成一步就更新对应项的状态, 让用户随时看到进展。同时只能有一项 in_progress。\n"
            "status 取值: pending (待办) / in_progress (进行中) / completed (已完成)。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "完整清单 (覆盖旧值)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": string_prop("步骤内容"),
                            "status": {"type": "string",
                                       "enum": list(_TODO_STATUSES),
                                       "description": "该步骤当前状态"},
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
        handler=_todo_write_handler,
        dangerous=False,
        group="session",
        read_only=True,
    )


def build_todo_read_tool() -> Tool:
    return Tool(
        name="todo_read",
        description="读取当前会话的任务清单与进度。",
        parameters={"type": "object", "properties": {}},
        handler=_todo_read_handler,
        dangerous=False,
        group="session",
        read_only=True,
    )


# ------------------------------------------------------------------ ask_user

def _ask_user_handler(ctx: ToolContext, question: Any = "", options: Any = None,
                      **_kwargs) -> str:
    q = str(question or "").strip()
    if not q:
        return "[错误] ask_user 需要 question 参数"
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]

    # 扩展点: TUI 可在 ctx.ui 上注入 ask_user 钩子 (当前内置 UI 未注入, 走终端交互)
    ui_handle = getattr(ctx, "ui", None)
    if ui_handle is not None and hasattr(ui_handle, "ask_user"):
        try:
            ans = ui_handle.ask_user(q, opts)
            return f"用户回答: {ans}"
        except Exception:  # noqa: BLE001
            pass

    import sys
    if sys.stdin.isatty() and sys.stdout.isatty():
        from rich.console import Console
        console = Console()
        console.print(f"\n[bold cyan]?[/] [bold]{q}[/]")
        if opts:
            for i, o in enumerate(opts, 1):
                console.print(f"  [cyan]{i}.[/] {o}")
            console.print("[dim](输入编号或自由作答; Ctrl+C 跳过)[/]")
        else:
            console.print("[dim](自由作答; Ctrl+C 跳过)[/]")
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return "用户未回答本次提问 (已跳过), 请基于现有信息继续并说明你的假设。"
        if not raw and opts:
            raw = opts[0]
        elif raw.isdigit() and opts and 1 <= int(raw) <= len(opts):
            raw = opts[int(raw) - 1]
        return f"用户回答: {raw or '(空)'}"

    # headless 降级: 无法提问, 把问题原样返回让模型自行决策并显式告知用户
    lines = ["[无法交互] 当前不是交互式终端, 用户无法实时回答。"]
    lines.append(f"问题: {q}")
    if opts:
        lines.append("选项: " + " | ".join(opts))
    lines.append("请基于现有信息选择最合理的方案继续执行, 并在最终回复中向用户说明该假设。")
    return "\n".join(lines)


def build_ask_user_tool() -> Tool:
    return Tool(
        name="ask_user",
        description=(
            "当需求存在多种合理方向、或缺失关键信息时, 主动向用户提问 (可带选项)。\n"
            "仅在真正需要用户拍板时使用; 交互式终端会阻塞等待回答, headless 环境\n"
            "会返回提示并由你自行决策。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": string_prop("要问的问题 (具体、可决策)"),
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 个候选选项 (可选, 缺省则自由作答)",
                },
            },
            "required": ["question"],
        },
        handler=_ask_user_handler,
        dangerous=False,
        group="session",
        read_only=True,
    )


# ------------------------------------------------------------------ enter/exit_plan_mode

def _enter_plan_handler(ctx: ToolContext, **_kwargs) -> str:
    if ctx.plan_mode:
        return "已处于 Plan 模式 (只读分析, 修改类工具被拦截)。"
    _set_plan_mode(ctx, True)
    return ("Plan 模式已开启: 只读分析, 修改类工具将被拦截。\n"
            "请充分调研后调用 exit_plan_mode 并附上完整实施计划。")


def _exit_plan_handler(ctx: ToolContext, plan: Any = "", **_kwargs) -> str:
    p = str(plan or "").strip()
    if not p:
        return "[错误] exit_plan_mode 必须在 plan 参数里给出完整的实施计划文本 (涉及哪些文件/函数、每步怎么改)"
    _set_plan_mode(ctx, False)
    return ("Plan 模式已关闭, 可以执行修改操作。\n"
            "===== 实施计划 (请按此执行) =====\n" + p + "\n"
            "=================================")


def build_enter_plan_mode_tool() -> Tool:
    return Tool(
        name="enter_plan_mode",
        description=(
            "进入 Plan 模式 (只读): 开启后所有修改类工具被拦截, 适合先彻底调研再动手的复杂任务。"
        ),
        parameters={"type": "object", "properties": {}},
        handler=_enter_plan_handler,
        dangerous=False,
        group="session",
        read_only=True,
    )


def build_exit_plan_mode_tool() -> Tool:
    return Tool(
        name="exit_plan_mode",
        description=(
            "退出 Plan 模式并提交实施计划。必须在 plan 参数里写出完整计划:\n"
            "改哪些文件的哪些函数、每步怎么改、备选方案与取舍。计划会展示给用户确认。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "plan": string_prop("完整的分步实施计划 (必填)"),
            },
            "required": ["plan"],
        },
        handler=_exit_plan_handler,
        dangerous=False,
        group="session",
        read_only=True,
    )


class SessionToolsPlugin(Plugin):
    """会话交互插件: 注册 todo_write / todo_read / ask_user / enter_plan_mode / exit_plan_mode。"""

    name = "tools.session"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(build_todo_write_tool())
        registry.register(build_todo_read_tool())
        registry.register(build_ask_user_tool())
        registry.register(build_enter_plan_mode_tool())
        registry.register(build_exit_plan_mode_tool())
