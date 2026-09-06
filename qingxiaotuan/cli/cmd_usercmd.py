"""`qxt usercmd` —— 列出用户自定义斜杠命令 (拆分自 cmd_services.py)。"""

from __future__ import annotations

import os

from ._ui_singleton import console
from ..ui.format import Table


def build_kernel(*a, **k):
    """惰性构建内核: 仅实际执行 usercmd 命令时才加载 app 链。"""
    from ..app import build_kernel as _f
    return _f(*a, **k)


def cmd_usercmd(args) -> int:
    """CLI: qxt usercmd [list] — 列出用户自定义斜杠命令。"""
    from ..cli.user_commands import load_user_commands
    workspace = getattr(args, "workspace", None) or os.getcwd()
    try:
        kernel = build_kernel(getattr(args, "profile", "default"))
    except Exception as exc:  # noqa: BLE001
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    table = load_user_commands(config, workspace)
    if not table:
        console.print("没有自定义斜杠命令。")
        console.print("放置 <home>/commands/*.md 或 <workspace>/.qxt/commands/*.md 即可注册 /<命令名>。")
        return 0
    t = Table(title="用户自定义斜杠命令")
    t.add_column("命令", justify="left")
    t.add_column("说明", justify="left")
    t.add_column("参数", justify="left")
    t.add_column("来源", justify="left")
    for uc in sorted(table.values(), key=lambda c: c.name):
        src = "项目级" if str(uc.path).find(os.sep + ".qxt" + os.sep) >= 0 else "用户级"
        t.add_row(f"/{uc.name}", uc.description, uc.argument_hint or "-", src)
    console.print(t)
    console.print("用法: 在会话中输入 /<命令名>  运行 (List 见 qxt usercmd list)")
    return 0
