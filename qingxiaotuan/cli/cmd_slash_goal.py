"""斜杠命令 /goal — Goal 模式。

拆分自 cmd_slash.py。
"""

from __future__ import annotations

from ._ui_singleton import ui


def _cmd_goal(agent, arg: str) -> None:
    """/goal [目标描述|clear] — Goal 模式: 循环执行直到目标满足。

    用法:
      /goal all tests pass and lint is clean   # 设置目标
      /goal                                     # 查看当前目标
      /goal clear                               # 清除目标
    """
    if not arg:
        # 查看当前目标
        if agent._goal:
            ui.info(f"  当前 Goal: {agent._goal}")
            ui.info("  使用 /goal clear 可清除目标")
        else:
            ui.info("  未设置 Goal。用法: /goal <目标描述>")
            ui.info("  示例: /goal all tests pass and lint is clean")
        return

    if arg.strip().lower() in ("clear", "off", "stop", "cancel"):
        agent.clear_goal()
        ui.success("Goal 模式已关闭。")
        return

    # 设置目标
    agent.set_goal(arg.strip())
    ui.success(f"Goal 已设置: {arg.strip()}")
    ui.info("代理将在每次对话后检查目标是否满足, 未满足则自动继续推进。")
    ui.info("使用 /goal clear 可随时清除目标。")
