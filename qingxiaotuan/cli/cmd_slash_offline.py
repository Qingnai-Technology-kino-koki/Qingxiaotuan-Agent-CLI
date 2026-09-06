"""斜杠命令 /offline — 离线模式 (Ollama) 管理。

拆分自 cmd_slash.py。
"""

from __future__ import annotations

from ._ui_singleton import ui


def _cmd_offline(agent, arg: str) -> None:
    """/offline [status|pull|list] — 离线模式管理。

    用法:
      /offline           显示离线模式状态
      /offline status    显示详细状态
      /offline pull <model>  下载模型
      /offline list      列出可用模型
    """
    parts = arg.split(None, 1) if arg else []
    action = parts[0].lower() if parts else "status"
    rest = parts[1] if len(parts) > 1 else ""

    if action == "status" or not action:
        ui.info("  [离线模式] 检查状态...")
        try:
            from ..models.offline import format_offline_status
            status_text = format_offline_status()
            ui.info(status_text)
        except Exception as exc:  # noqa: BLE001
            ui.error(f"检查离线状态失败: {exc}")
        return

    if action == "pull":
        if not rest:
            ui.error("用法: /offline pull <model>")
            ui.info("示例: /offline pull qwen2.5:7b")
            return
        ui.info(f"  [离线模式] 下载模型: {rest}")
        ui.info("  这可能需要几分钟，取决于模型大小...")
        try:
            from ..models.offline import get_ollama_manager
            manager = get_ollama_manager()

            def on_progress(msg):
                ui.info(f"    {msg}")

            success = manager.pull_model(rest, callback=on_progress)
            if success:
                ui.success(f"模型 {rest} 下载完成")
            else:
                ui.error(f"模型 {rest} 下载失败")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"下载失败: {exc}")
        return

    if action == "list":
        ui.info("  [离线模式] 可用模型:")
        try:
            from ..models.offline import get_ollama_manager
            manager = get_ollama_manager()
            models = manager.list_available_models()
            for m in models:
                ui.info(f"    • {m}")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"获取模型列表失败: {exc}")
        return

    ui.error(f"未知操作: {action}. 可用: status / pull / list")
