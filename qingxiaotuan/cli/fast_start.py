"""快速启动器 —— 加载屏内嵌于 QxtTUI (单 Application), 不再使用独立 SplashTUI。

启动流程:
  1. <50ms: QxtTUI 立即弹出, 显示内嵌加载屏 (内核在后台线程构建)
  2. 后台线程构建 kernel (~0.5s)
  3. 就绪: QxtTUI 自动切换为对话界面 (同一事件循环, 无双屏 alt-screen 切换崩溃)

单一 Application 是根治 Windows Terminal(conpty) 下 Splash→TUI 闪退的关键。
"""

from __future__ import annotations


def fast_chat(args: object) -> int:
    """快速启动交互式对话 (加载屏内嵌于 TUI, 兼容旧入口)。"""
    from .cmd_chat import cmd_chat
    return int(cmd_chat(args))
