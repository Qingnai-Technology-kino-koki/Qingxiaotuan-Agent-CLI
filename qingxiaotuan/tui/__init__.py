"""青小团统一 TUI (QxtTUI)。

单一模块 tui.py 实现 Kimi Code CLI 视觉皮肤 + qingxiaotuan 自有 Python 后端。
仅依赖 rich + prompt_toolkit，开箱即用，无 Node 依赖。

惰性导出 (PEP 562): prompt_toolkit 的导入成本推迟到真正使用时。
"""

from __future__ import annotations

import importlib

__all__ = ["QxtTUI"]


def __getattr__(name: str):
    if name == "QxtTUI":
        mod = importlib.import_module(".tui", __name__)
        return getattr(mod, "QxtTUI")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
