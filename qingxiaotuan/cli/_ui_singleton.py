"""CLI 共享 UI 单例 —— 所有 cmd_* 模块引用同一个 ui 对象。

确保 tests 中 mock "qingxiaotuan.cli.commands.ui" 能覆盖所有子模块的 ui 调用。

ui 采用惰性代理: 完整 UI (repl, 依赖 prompt_toolkit ~0.5s) 仅在首次真正使用
交互能力时才导入。非交互命令 (qxt mcp / qxt setup / qxt session 等) 只用到
console, 启动不会加载 prompt_toolkit。
"""

from __future__ import annotations

from ..ui.plain_console import console

_ui = None


def _get_ui():
    global _ui
    if _ui is None:
        from ..ui import UI

        _ui = UI(console=console)
    return _ui


class _UiProxy:
    """惰性 UI 代理: 首次访问任意属性时才真正导入 repl。"""

    __slots__ = ()

    def __getattr__(self, name):
        return getattr(_get_ui(), name)


ui = _UiProxy()
