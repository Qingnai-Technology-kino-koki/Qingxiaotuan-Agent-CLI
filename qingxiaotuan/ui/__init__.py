"""UI 子系统 —— 采用 Kimi Code CLI 风格的终端 TUI (青小团专属配色)。

- 基于 prompt_toolkit 的多行输入 + 持久命令历史 (Enter 发送 / Esc+Enter 换行)。
- 基于 Rich 的实时输出: 思考过程 (思维链)、工具调用状态 (▶ 前缀 + 树形)、阶段面板、进度汇报。
"""

from .repl import UI
from .fullscreen import FullScreenTUI

__all__ = ["UI", "FullScreenTUI"]
