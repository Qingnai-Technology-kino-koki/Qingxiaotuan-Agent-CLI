"""UI 子系统 —— 统一 TUI (tui.QxtTUI) + 向后兼容别名。

统一 TUI (qingxiaotuan.tui.tui.QxtTUI) 融合了多套设计的精华:
  1. Kimi Code CLI: 三区布局、流式 delta、Moon spinner、工具状态
  2. qxt 原版: 吉祥物状态机、圆角框式横幅、上下文占用条
  3. fullscreen: 侧栏 focus 循环、context bar 可视化

向后兼容:
  - QxtTUI  -> qingxiaotuan.tui.tui.QxtTUI (主力, cmd_chat.py 使用)
  - UI        -> repl.UI (REPL 模式, 命令行无 --tui 时使用)

惰性导出 (PEP 562): prompt_toolkit ~0.5s 导入成本推迟到真正使用时。
"""

from __future__ import annotations

import importlib

_TUI_EXPORTS = {
    # 主力: 统一 TUI (已迁移至 qingxiaotuan/tui/ 包)
    "QxtTUI": ("..tui.tui", "QxtTUI"),

    # REPL 模式 (非 TUI, 用于 cmd_chat.py 的普通 REPL 路径)
    "UI": (".repl", "UI"),

    # 模块级引用 (向后兼容 import)
    "repl": (".repl", None),
    "tui": ("..tui.tui", None),
}

__all__ = [
    "QxtTUI",
    "UI",
    "repl",
    "tui",
]


def __getattr__(name: str):
    entry = _TUI_EXPORTS.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    submodule, attr = entry
    mod = importlib.import_module(submodule, __name__)
    return getattr(mod, name) if attr is None else getattr(mod, attr)
