"""UI 子系统 —— DeepSeek 风格终端工作台。

- 基于 prompt_toolkit 的多行输入 + 持久命令历史 (Enter 发送 / Esc+Enter 换行)。
- 基于 Rich 的实时输出: 双线框欢迎界面 + 输入框 + 状态栏。
"""

from .repl import UI
from .fullscreen import FullScreenTUI

__all__ = ["UI", "FullScreenTUI"]
