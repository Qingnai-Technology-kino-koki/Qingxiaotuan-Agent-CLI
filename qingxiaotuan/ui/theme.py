"""统一主题: repl 与 fullscreen 共用同一套配色与状态图标。

设计原则: hex 色为唯一事实来源 (与 mascot._PALETTE 同源), 再派生两套表达:
  * C         -> Rich 颜色名 (repl.py 用)
  * PT_STYLE  -> prompt_toolkit 样式表 (fullscreen.py 用)
"""

from __future__ import annotations

# ---------------------------------------------------------------- 主色 (hex)
ACCENT = "#2DD4BF"      # 莫奈青 (主强调)
ACCENT_BRIGHT = "#5EEAD4"
BLUE = "#38BDF8"        # 思考蓝
TEXT = "#E8EEF2"        # 正文
DIM = "#8AA4B8"         # 次级信息
MUTED = "#5B7285"       # 弱化信息
OK = "#34D399"
WARN = "#FBBF24"
ERR = "#F87171"
BOX = "#4B6A80"         # 边框
PRIMARY = "#63D7C5"     # 标题

# ---------------------------------------------------------------- Rich 颜色名
C = {
    "accent": "cyan",
    "text": "bright_white",
    "dim": "grey66",
    "muted": "grey50",
    "ok": "green",
    "warn": "yellow",
    "err": "red",
    "box": "grey46",
    "primary": "bright_cyan",
}

# ---------------------------------------------------------------- prompt_toolkit 样式
PT_STYLE = {
    "status": "bg:#16324f #d9f0ff",
    "title": "bold #63d7c5",
    "panel": "#8aa4b8",
    "log": "#e8eef2",
    "input": "#f5f7f8",
    "bottom-toolbar": "#8aa4b8",
    "context-ok": "#34d399",
    "context-warn": "#fbbf24",
    "context-err": "#f87171",
}

# ---------------------------------------------------------------- 吉祥物状态图标
MASCOT_ICONS = {
    "idle": "◦",
    "thinking": "◍",
    "working": "●",
    "alert": "⚠",
    "done": "✓",
}


def context_bar(pct: float, bar_len: int = 20) -> str:
    """按百分比生成 █░ 占用条 (0-100 钳制)。"""
    pct = max(0.0, min(100.0, float(pct)))
    filled = int(round(pct / 100.0 * bar_len))
    return "█" * filled + "░" * (bar_len - filled)


def context_style(pct: float) -> str:
    """占用百分比对应的语义级: 高占用变红。返回 ok/warn/err。

    repl 直接用 C[level]; fullscreen 拼成 "context-{level}" 对应 PT_STYLE 类。
    """
    if pct >= 80:
        return "err"
    if pct >= 50:
        return "warn"
    return "ok"
