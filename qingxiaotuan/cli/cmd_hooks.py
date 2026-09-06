"""`qxt hooks` 与 `/hooks` —— 用户级 Hooks 管理 (拆分自 cmd_services.py)。"""

from __future__ import annotations

import json
import os

from ..hooks import HookManager, HOOK_EVENTS
from ._ui_singleton import console, ui


def build_kernel(*a, **k):
    """惰性构建内核: 仅实际执行 hooks 命令时才加载 app 链。"""
    from ..app import build_kernel as _f
    return _f(*a, **k)


def cmd_hooks(args) -> int:
    """用户级 Hooks 管理: 列出 / 测试。"""
    sub = getattr(args, "hook_cmd", None)
    workspace = getattr(args, "workspace", None) or os.getcwd()
    if sub == "list":
        return _hooks_list(workspace)
    if sub == "test":
        return _hooks_test(workspace, args)
    console.print("未知子命令。可用: list / test")
    return 1


def _hooks_list(workspace: str) -> int:
    try:
        kernel = build_kernel()
        config = kernel.require("config")
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    mgr = HookManager(config, workspace, kernel=kernel)
    if not mgr.enabled or not mgr.list_hooks():
        console.print("未配置任何 Hooks。在配置中 hooks.<事件> 下添加命令即可 "
                      "(事件: PreToolUse / PostToolUse / SessionStart / SessionEnd)。")
        return 0
    for evt in HOOK_EVENTS:
        specs = [h for h in mgr.list_hooks() if h["event"] == evt]
        if not specs:
            continue
        console.print(f"{evt}")
        for h in specs:
            flag = "阻断" if (h["blocking"] and mgr.allow_blocking) else "审计"
            edit = "可改参" if (h["allow_edit_args"] or mgr.allow_edit_args) else "只读"
            console.print(f"  matcher={h['matcher']!r}  {flag}/{edit}  cmd={' '.join(h['command'])}")
            if h["description"]:
                console.print(f"    {h['description']}")
    return 0


def _hooks_test(workspace: str, args) -> int:
    event = getattr(args, "event", None) or "PreToolUse"
    tool = getattr(args, "tool", None) or "echo_text"
    raw = getattr(args, "tool_input", None) or "{}"
    try:
        payload = json.loads(raw)
    except Exception as exc:
        console.print(f"--json 不是合法 JSON: {exc}")
        return 1
    try:
        kernel = build_kernel()
        config = kernel.require("config")
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    mgr = HookManager(config, workspace, kernel=kernel)
    if not mgr.enabled_for(event):
        console.print(f"事件 {event} 未配置任何 hook。")
        return 0
    console.print(f"触发 {event} (tool={tool}) ...")
    if event == "PreToolUse":
        d = mgr.run_pre(tool, payload, dangerous=True)
        console.print(f"  block={d.block}  reason={d.reason!r}  args={d.args!r}")
    elif event == "PostToolUse":
        mgr.run_post(tool, payload, None)
        console.print("  PostToolUse 已触发 (见 hook 脚本输出/审计事件)。")
    else:
        mgr.run_session(event, {"tool": tool, **payload})
        console.print(f"  {event} 已触发。")
    return 0


def _cmd_hooks(agent, arg: str) -> None:
    """/hooks: 列出当前会话生效的 Hooks。"""
    hooks = getattr(getattr(agent, "ctx", None), "hooks", None)
    if hooks is None or not hooks.enabled:
        ui.info("未启用 Hooks。配置中设置 hooks.enabled=true 并添加 hooks.<事件>。")
        return
    specs = hooks.list_hooks()
    if not specs:
        ui.info("已启用 Hooks, 但当前未配置任何规则。")
        return
    for evt in HOOK_EVENTS:
        rows = [h for h in specs if h["event"] == evt]
        if not rows:
            continue
        ui.info(f"  {evt}:")
        for h in rows:
            flag = "阻断" if (h["blocking"] and hooks.allow_blocking) else "审计"
            edit = "可改参" if (h["allow_edit_args"] or hooks.allow_edit_args) else "只读"
            ui.info(f"    matcher={h['matcher']}  [{flag}/{edit}]  {' '.join(h['command'])}")
