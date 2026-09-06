"""斜杠命令 /sandbox — 沙箱执行。

拆分自 cmd_slash.py。
"""

from __future__ import annotations

from ._ui_singleton import ui


def _cmd_sandbox(agent, arg: str) -> None:
    """/sandbox [run|snapshot|diff|cleanup] [args] — 沙箱执行。

    用法:
      /sandbox run <command>              在 Docker 沙箱中执行命令
      /sandbox snapshot <paths>           创建文件快照
      /sandbox diff <snap_id>             查看差异
      /sandbox cleanup                    清理沙箱
    """
    parts = arg.split(None, 1) if arg else []
    action = parts[0].lower() if parts else "help"
    rest = parts[1] if len(parts) > 1 else ""

    if action == "help" or not action:
        ui.info("  /sandbox 用法:")
        ui.info("    /sandbox run <command>      在 Docker 沙箱中执行命令")
        ui.info("    /sandbox snapshot <paths>   创建文件快照 (逗号分隔)")
        ui.info("    /sandbox diff <snap_id>     查看差异")
        ui.info("    /sandbox cleanup            清理沙箱")
        ui.info("")
        ui.info("  示例:")
        ui.info("    /sandbox run rm -rf /tmp/test")
        ui.info("    /sandbox snapshot src/main.py,src/utils.py")
        ui.info("  说明: 沙箱在 Docker 容器中执行命令，隔离危险操作")
        return

    if action == "run":
        if not rest:
            ui.error("用法: /sandbox run <command>")
            return
        ui.info(f"  [沙箱] 正在执行: {rest}")
        try:
            from ..tools.sandbox import sandbox_run
            ctx = agent.ctx
            result = sandbox_run(ctx, rest)
            ui.answer_md(f"```\n{result}\n```")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"沙箱执行失败: {exc}")
        return

    if action == "snapshot":
        if not rest:
            ui.error("用法: /sandbox snapshot <paths> (逗号分隔)")
            return
        ui.info(f"  [沙箱] 创建快照: {rest}")
        try:
            from ..tools.sandbox import sandbox_snapshot
            ctx = agent.ctx
            result = sandbox_snapshot(ctx, rest)
            ui.success(result)
        except Exception as exc:  # noqa: BLE001
            ui.error(f"快照失败: {exc}")
        return

    if action == "diff":
        if not rest:
            ui.error("用法: /sandbox diff <snap_id>")
            return
        ui.info(f"  [沙箱] 查看差异: {rest}")
        try:
            from ..tools.sandbox import sandbox_diff
            ctx = agent.ctx
            result = sandbox_diff(ctx, rest)
            ui.info(result)
        except Exception as exc:  # noqa: BLE001
            ui.error(f"差异查看失败: {exc}")
        return

    if action == "cleanup":
        ui.info("  [沙箱] 清理中...")
        try:
            from ..tools.sandbox import sandbox_cleanup
            ctx = agent.ctx
            result = sandbox_cleanup(ctx)
            ui.success(result)
        except Exception as exc:  # noqa: BLE001
            ui.error(f"清理失败: {exc}")
        return

    ui.error(f"未知操作: {action}. 可用: run / snapshot / diff / cleanup")
