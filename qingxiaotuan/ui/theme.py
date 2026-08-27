"""统一主题: repl 与 fullscreen 共用同一套配色与状态图标。

配色采用 Kimi Code CLI 官方 dark 主题 token (moonshotai.github.io/kimi-code):
primary=#4FA8FF / accent=#5BC0BE / text=#E0E0E0 / textDim=#888888 /
textMuted=#6B6B6B / border=#5A5A5A / success=#4EC87E / warning=#E8A838 /
error=#E85454 / roleUser=#FFCB6B / shellMode=#BD93F9。

设计原则 (Kimi Code 样式, 唯一强调色):
  * 只保留 #4FA8FF (primary 浅蓝) 作为唯一强调色, 用于标题/徽章/提示符;
  * 其余全部无样式 (无颜色/无加粗/无下划线), 彻底无高亮;
  * C         -> Rich 颜色名 (repl.py 用)
  * PT_STYLE  -> prompt_toolkit 样式表 (fullscreen.py 用)
"""

from __future__ import annotations

# 唯一强调色: Kimi Code primary 浅蓝 (其余颜色一律不用)
ACCENT_COLOR = "#4FA8FF"

# ---------------------------------------------------------------- Rich 颜色名 (仅 primary/accent 用浅蓝, 其余无样式)
C = {
    "accent": ACCENT_COLOR,
    "primary": ACCENT_COLOR,
    "text": "",
    "dim": "",
    "muted": "",
    "ok": "",
    "warn": "",
    "err": "",
    "box": "",
}

# ---------------------------------------------------------------- prompt_toolkit 样式 (仅标题/徽章/提示符用浅蓝, 其余无样式)
PT_STYLE = {
    "status": "",
    "title": ACCENT_COLOR,
    "panel": "",
    "log": "",
    "input": "",
    "bottom-toolbar": "",
    "badge": ACCENT_COLOR,
    "badge-plan": ACCENT_COLOR,
    "badge-yolo": ACCENT_COLOR,
    "prompt": ACCENT_COLOR,
    "text-area.prompt": ACCENT_COLOR,
    "context-ok": "",
    "context-warn": "",
    "context-err": "",
}


# prompt_toolkit 样式解析有继承机制 (completion-menu.completion 会回退到父类
# completion-menu), 空字符串 "" 不会重置属性, 必须显式重置全部属性。
_PT_RESET = "fg:default bg:default nobold noitalic nounderline nostrike noreverse noblink nohidden"


def blank_pt_style() -> dict:
    """生成覆盖 prompt_toolkit 全部默认样式的样式表。

    标题/徽章/提示符保留浅蓝强调色 (Kimi Code 唯一强调色), 其余全部显式
    重置为无样式 (彻底无高亮)。prompt_toolkit 会合并自带默认样式 (补全菜单/
    滚动条/光标列等带颜色), 仅置空自定义键不够, 需把默认样式表的所有规则键
    也显式重置。
    """
    base = {k: _PT_RESET for k in PT_STYLE}
    for k in ("title", "badge", "badge-plan", "badge-yolo", "prompt", "text-area.prompt"):
        base[k] = ACCENT_COLOR
    try:
        from prompt_toolkit.styles.defaults import default_ui_style
        for cls, _ in default_ui_style().style_rules:
            base.setdefault(cls, _PT_RESET)
    except Exception:  # prompt_toolkit 不可用时退化为自定义键
        pass
    return base

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
