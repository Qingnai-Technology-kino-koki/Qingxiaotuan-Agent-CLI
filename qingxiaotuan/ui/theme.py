"""统一主题 —— 忠实采用 Kimi Code CLI 的原版设计令牌 (无改动)。

本模块的色彩 / 符号 / spinner / diff 颜色全部取自 kimi-code `src/tui`, 与
Kimi Code CLI 原版视觉完全一致:
  * theme/colors.ts (darkColors): primary=#4FA8FF, accent=#5BC0BE …
  * constant/symbols.ts: STATUS_BULLET='● ', USER_MESSAGE_BULLET='✨ ' …
  * constant/rendering.ts: MOON_SPINNER(120ms) / BRAILLE_SPINNER(80ms)

设计原则 (与 Kimi Code 原版一致):
  * 唯一品牌色 primary=#4FA8FF 用于标题/徽章/提示符;
  * 其余按角色取色 (text/roleUser/success/warning/error/shellMode …);
  * C         -> Rich 颜色名 (repl.py 用)
  * PT_STYLE  -> prompt_toolkit 样式表 (fullscreen.py 用)
  * BRAND     -> 品牌元信息 (名称/标语/版本色), GUI/TUI 共用

注意: 产品名仍为「青小团」, 但 TUI 视觉令牌 (颜色/符号/布局) 与 Kimi Code
原版完全一致 —— 即 qingxiaotuan-cli 是对 Kimi Code 的 rebrand, 视觉沿用原版。
"""

from __future__ import annotations

import os

# ================================================================ 品牌元信息
# 产品身份保留青小团, TUI 视觉令牌采用 Kimi Code 原版。
BRAND = {
    "name": "青小团",
    "name_en": "Qingxiaotuan",
    "tagline": "终端智能体 · 自主借鉴 · 安全可控",
    "primary": "#4FA8FF",   # Kimi Code 原版品牌主色
    "accent": "#5BC0BE",    # Kimi Code 原版次要高亮
}

# Kimi Code 原版唯一品牌色
ACCENT_COLOR = BRAND["primary"]

# ---------------------------------------------------------------- Rich 颜色名 (Kimi Code dark 主题)
# 保留 repl.py / fullscreen.py 已用的键名 (accent/dim/box/primary/text/muted/err/ok/warn),
# 并补齐 Kimi 原版角色色。
C = {
    "primary": "#4FA8FF",
    "accent": "#5BC0BE",
    "text": "#E0E0E0",
    "textstrong": "#F5F5F5",
    "textdim": "#888888",
    "textmuted": "#6B6B6B",
    "dim": "#888888",          # = textDim (repl.py 兼容别名)
    "muted": "#6B6B6B",        # = textMuted (repl.py 兼容别名)
    "border": "#5A5A5A",
    "borderfocus": "#E8A838",
    "box": "#5A5A5A",          # 外框 (repl.py 用, = border)
    "ok": "#4EC87E",
    "warn": "#E8A838",
    "err": "#E85454",
    "roleuser": "#FFCB6B",
    "shellmode": "#BD93F9",
}

# ---------------------------------------------------------------- prompt_toolkit 样式 (Kimi 原版)
# 仅标题/徽章/提示符用 primary 蓝, 其余无样式 (继承终端默认)。
PT_STYLE = {
    "status": "",
    "title": "#4FA8FF",
    "panel": "",
    "log": "",
    "input": "",
    "bottom-toolbar": "",
    "badge": "#4FA8FF",
    "badge-plan": "#4FA8FF",
    "badge-yolo": "#E8A838",
    "prompt": "#4FA8FF",
    "text-area.prompt": "#4FA8FF",
    "context-ok": "",
    "context-warn": "",
    "context-err": "",
}


# prompt_toolkit 样式解析有继承机制 (completion-menu.completion 会回退到父类
# completion-menu), 空字符串 "" 不会重置属性, 必须显式重置全部属性。
_PT_RESET = "fg:default bg:default nobold noitalic nounderline noreverse noblink nohidden"


def blank_pt_style() -> dict:
    """生成覆盖 prompt_toolkit 全部默认样式的样式表。

    标题/徽章/提示符保留 Kimi 原版 primary 蓝, 其余全部显式重置为无样式
    (彻底无高亮)。prompt_toolkit 会合并自带默认样式 (补全菜单/滚动条/光标列等
    带颜色), 仅置空自定义键不够, 需把默认样式表的所有规则键也显式重置。
    """
    base = {k: _PT_RESET for k in PT_STYLE}
    for k in ("title", "badge", "badge-plan", "badge-yolo", "prompt", "text-area.prompt"):
        base[k] = "#4FA8FF"
    try:
        from prompt_toolkit.styles.defaults import default_ui_style
        for cls, _ in default_ui_style().style_rules:
            base.setdefault(cls, _PT_RESET)
    except Exception:  # prompt_toolkit 不可用时退化为自定义键
        pass
    return base


# ---------------------------------------------------------------- 背景检测 (暗/亮)
def detect_background() -> str:
    """探测终端背景明暗, 返回 'dark' 或 'light'。

    优先读 COLORFGBG (形如 "15;0" 末位为默认背景色号, 0/1 黑=暗, 其余=亮),
    其次 WT_SESSION / VSCODE / 256color 等环境暗示, 默认 'dark'。
    """
    fg_bg = os.environ.get("COLORFGBG", "")
    if fg_bg:
        parts = fg_bg.split(";")
        if parts:
            last = parts[-1].strip()
            if last.isdigit():
                # 0=black, 1=red(暗), 15=white(亮); 末位 >=8 多为亮背景
                return "light" if int(last) >= 8 else "dark"
    for key in ("WT_SESSION", "VSCODE_PID", "TERM_PROGRAM"):
        if os.environ.get(key):
            return "dark"
    term = os.environ.get("TERM", "")
    if "256color" in term or "truecolor" in term or "xterm" in term:
        return "dark"
    return "dark"


# ---------------------------------------------------------------- Kimi 原版符号 (src/tui/constant/symbols.ts)
STATUS_BULLET = "● "          # 助手/状态消息前缀 (U+25CF, 避免 emoji 回退)
USER_MESSAGE_BULLET = "✨ "   # 用户消息前缀
SUCCESS_MARK = "✓ "           # 成功
FAILURE_MARK = "✗ "           # 失败
ABORTED_MARK = "⊘"            # 中止
SELECT_POINTER = "❯"          # 列表当前行指针
CURRENT_MARK = "← current"    # 当前列表值后缀

# Kimi 输入提示符 (normal '>' / bash '!')
PROMPT_AGENT = "> "
PROMPT_PLAN = "📋 > "
PROMPT_SHELL = "! "

# ---------------------------------------------------------------- Spinner (src/tui/constant/rendering.ts)
MOON_SPINNER_FRAMES = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
MOON_SPINNER_INTERVAL_MS = 120
BRAILLE_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
BRAILLE_SPINNER_INTERVAL_MS = 80

# ---------------------------------------------------------------- 吉祥物状态图标 (与 Kimi 状态语义对齐)
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


# ---------------------------------------------------------------- Diff 颜色 (Kimi Code 原版, src/tui/theme/colors.ts)
DIFF_ADDED = "#4EC87E"          # + 新增行 (diffAdded)
DIFF_REMOVED = "#E85454"        # - 删除行 (diffRemoved)
DIFF_ADDED_STRONG = "#7AD99B"   # 新增行 (高亮/选中) (diffAddedStrong)
DIFF_REMOVED_STRONG = "#F08585"  # 删除行 (高亮/选中) (diffRemovedStrong)
DIFF_GUTTER = "#6B6B6B"         # 行号槽 (diffGutter)
DIFF_META = "#888888"           # @@ hunk 元信息 (diffMeta)
DIFF_CONTEXT = "#888888"        # 上下文行 (textDim)
DIFF_HEADER = "#4FA8FF"         # diff 头 (取 primary, fullscreen 兼容)
DIFF_HUNK = "#BD93F9"           # @@ hunk 标记 (取 shellMode, fullscreen 兼容)
