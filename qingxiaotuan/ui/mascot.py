"""青小团吉祥物 — block art logo。

设计语言 (对齐 TUI):
  参考实现的终端 TUI 没有 ASCII 脸吉祥物,
  使用的是 block art logo: ▐█▛█▛█▌ / ▐█████▌
  配合 moon phase spinner (🌑🌒🌓🌔🌕🌖🌗🌘) 做状态动画。

  本模块提供:
  * logo()   -> block art logo (两行)
  * spinner() -> moon phase spinner frame
  * state_icon() -> 单字符状态图标
  * svg()    -> SVG 动态组件 (用于富 UI / README)

状态由 UI 层驱动: thinking->moon spinner, working->moon spinner,
安全拦截 -> alert icon, 任务结束 -> done icon, 空闲 -> idle icon。
"""

from __future__ import annotations

import math
import time

# 状态常量
IDLE = "idle"
THINKING = "thinking"
WORKING = "working"
ALERT = "alert"
DONE = "done"

STATES = (IDLE, THINKING, WORKING, ALERT, DONE)

# 配色 (dark palette)
_PRIMARY = "#4FA8FF"
_ACCENT = "#5BC0BE"
_SUCCESS = "#4EC87E"
_WARNING = "#E8A838"
_ERROR = "#E85454"

# Moon phase spinner frames (原版)
MOON_FRAMES = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
MOON_INTERVAL_MS = 120

# Braille spinner (备用)
BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# block art logo (原版)
_LOGO_LINE1 = "▐█▛█▛█▌"
_LOGO_LINE2 = "▐█████▌"

# 状态图标 (单字符)
_STATE_ICONS = {
    IDLE: "◦",
    THINKING: "◍",
    WORKING: "●",
    ALERT: "⚠",
    DONE: "✓",
}

# SVG 配色
_SVG_PALETTE = {
    IDLE:     {"body": _PRIMARY, "dark": "#1E5FA8", "eye": "#0B2540", "accent": _ACCENT},
    THINKING: {"body": _PRIMARY, "dark": "#1E5FA8", "eye": "#0B2540", "accent": "#7AD99B"},
    WORKING:  {"body": _SUCCESS, "dark": "#1E7A48", "eye": "#053B2A", "accent": "#7AD99B"},
    ALERT:    {"body": _WARNING, "dark": "#92660A", "eye": "#3B1A06", "accent": "#F08585"},
    DONE:     {"body": _ACCENT,  "dark": "#1E6E6C", "eye": "#0B3B38", "accent": "#7AD99B"},
}


class Mascot:
    """青小团吉祥物: block art logo + moon spinner + SVG。"""

    def __init__(self, state: str = IDLE) -> None:
        self.state = state if state in STATES else IDLE
        self._frame = 0
        self._started_at = time.monotonic()

    # ---------------------------------------------------------- 状态控制
    def set(self, state: str) -> "Mascot":
        if state in STATES:
            self.state = state
        return self

    def tick(self) -> int:
        """推进动画帧计数。返回当前帧序号。"""
        self._frame = (self._frame + 1) % len(MOON_FRAMES)
        return self._frame

    @property
    def color(self) -> str:
        """当前状态对应的主色。"""
        return _SVG_PALETTE[self.state]["body"]

    # ---------------------------------------------------------- Block Art Logo
    def logo(self) -> tuple[str, str]:
        """返回 block art logo (两行)。

        >>> m = Mascot()
        >>> m.logo()
        ('▐█▛█▛█▌', '▐█████▌')
        """
        return _LOGO_LINE1, _LOGO_LINE2

    # ---------------------------------------------------------- Spinner
    def spinner(self, frame: int | None = None) -> str:
        """返回当前 moon phase spinner frame。

        >>> m = Mascot()
        >>> m.spinner(0) in MOON_FRAMES
        True
        """
        f = self._frame if frame is None else (frame % len(MOON_FRAMES))
        return MOON_FRAMES[f]

    def braille_spinner(self, frame: int | None = None) -> str:
        """返回 braille spinner frame (备用)。"""
        f = self._frame if frame is None else (frame % len(BRAILLE_FRAMES))
        return BRAILLE_FRAMES[f]

    # ---------------------------------------------------------- 状态图标
    def state_icon(self) -> str:
        """返回当前状态的单字符图标。

        >>> m = Mascot('done')
        >>> m.state_icon()
        '✓'
        """
        return _STATE_ICONS.get(self.state, "◦")

    def icon(self) -> str:
        """state_icon() 的别名 (向后兼容)。"""
        return self.state_icon()

    # ---------------------------------------------------------- ASCII 表示 (风格)
    def ascii(self, frame: int | None = None, color: bool = True) -> str:
        """返回 风格的 ASCII 表示。

        与原版不同: 不再使用 ASCII 脸, 而是 block art logo + 状态 spinner。
        适合嵌在侧栏或 banner。

        布局:
          ▐█▛█▛█▌  🌑  ← logo + spinner
          ▐█████▌  ●   ← logo + 状态图标
        """
        f = self._frame if frame is None else (frame % len(MOON_FRAMES))
        spin = MOON_FRAMES[f]
        icon = _STATE_ICONS.get(self.state, "◦")
        line1 = f"  {_LOGO_LINE1}  {spin}"
        line2 = f"  {_LOGO_LINE2}  {icon}"
        return f"{line1}\n{line2}"

    # ---------------------------------------------------------- SVG 动态
    def svg(self, size: int = 120) -> str:
        """返回自包含 SVG (含 SMIL 动画), 状态不同动画不同。

        用于富 UI / 文档 / README。viewBox 0 0 120 120。
        """
        p = _SVG_PALETTE[self.state]
        body, dark, eye, accent = p["body"], p["dark"], p["eye"], p["accent"]
        if self.state == THINKING:
            anim = self._svg_thinking(accent)
        elif self.state == WORKING:
            anim = self._svg_working(accent)
        elif self.state == ALERT:
            anim = self._svg_alert(accent)
        elif self.state == DONE:
            anim = self._svg_done(accent)
        else:
            anim = self._svg_idle(accent)

        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="{size}" height="{size}">
  <defs>
    <radialGradient id="bg" cx="50%" cy="40%" r="70%">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="{accent}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <circle cx="60" cy="60" r="54" fill="url(#bg)"/>
  <g>
    {anim}
    <ellipse cx="60" cy="68" rx="34" ry="32" fill="{body}" stroke="{dark}" stroke-width="3"/>
    <ellipse cx="60" cy="68" rx="34" ry="32" fill="{accent}" opacity="0.12"/>
    <circle cx="49" cy="64" r="5.5" fill="{eye}"/>
    <circle cx="71" cy="64" r="5.5" fill="{eye}"/>
    <circle cx="44" cy="78" r="4" fill="{accent}" opacity="0.6"/>
    <circle cx="76" cy="78" r="4" fill="{accent}" opacity="0.6"/>
    {self._svg_mouth(eye)}
  </g>
</svg>'''

    def _svg_mouth(self, eye: str) -> str:
        if self.state == DONE:
            return f'<path d="M50 82 Q60 92 70 82" stroke="{eye}" stroke-width="3" fill="none" stroke-linecap="round"/>'
        if self.state == ALERT:
            return f'<path d="M50 86 Q60 78 70 86" stroke="{eye}" stroke-width="3" fill="none" stroke-linecap="round"/>'
        if self.state == THINKING:
            return f'<circle cx="60" cy="84" r="3" fill="{eye}"/>'
        return f'<path d="M53 83 Q60 88 67 83" stroke="{eye}" stroke-width="3" fill="none" stroke-linecap="round"/>'

    @staticmethod
    def _svg_idle(accent: str) -> str:
        return '''<animateTransform attributeName="transform" type="translate" values="0 0; 0 -3; 0 0" dur="3s" repeatCount="indefinite"/>'''

    @staticmethod
    def _svg_thinking(accent: str) -> str:
        return '''<animateTransform attributeName="transform" type="rotate" values="-4 60 68; 4 60 68; -4 60 68" dur="0.9s" repeatCount="indefinite"/>'''

    @staticmethod
    def _svg_working(accent: str) -> str:
        return ('''<animateTransform attributeName="transform" type="translate" values="0 0; 0 -6; 0 0" dur="0.6s" repeatCount="indefinite"/>'''
                f'<circle cx="96" cy="40" r="7" fill="none" stroke="{accent}" stroke-width="3">'
                '<animate attributeName="stroke-dashoffset" from="0" to="44" dur="1s" repeatCount="indefinite"/>'
                '<animateTransform attributeName="transform" type="rotate" from="0 96 40" to="360 96 40" dur="1s" repeatCount="indefinite"/>'
                '</circle>')

    @staticmethod
    def _svg_alert(accent: str) -> str:
        return '''<animateTransform attributeName="transform" type="translate" values="-2 0; 2 0; -2 0" dur="0.18s" repeatCount="indefinite"/>'''

    @staticmethod
    def _svg_done(accent: str) -> str:
        return ('''<animateTransform attributeName="transform" type="translate" values="0 0; 0 -4; 0 0" dur="1.2s" repeatCount="indefinite"/>'''
                f'<text x="92" y="42" font-size="16" fill="{accent}">✦</text>')
