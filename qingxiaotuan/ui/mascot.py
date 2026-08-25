"""青小团动态吉祥物 (Mascot)。

设计语言
--------
一只圆润的「青色小团子」: 呼应项目名 Qingxiaotuan, 青色基调, 极简几何造型,
跨平台终端都能渲染 (不依赖图片)。它有"生命体征"——随任务推进切换状态:

  idle     待命  —— 平静呼吸, 圆眼
  thinking 思考  —— 眼睛转圈 / 脉冲, 头顶冒问号
  working  干活  —— 身体上下浮动 + 进度环, 眼睛专注
  alert    警戒  —— 变橙、抖动, 出现在 safety 拦截时 (最小影响半径生效)
  done     完成  —— 眼睛变弯月, 头顶小勾

两套表达:
  * ascii 帧动画  -> 纯键盘流 CLI (嵌入 banner / 状态栏), ANSI 着色
  * svg 动态组件  -> 富 UI / 文档 / README, 用 SMIL/CSS 动画随状态切换

状态由 UI 层驱动: think_start()->thinking, tool_call()->working,
安全拦截 -> alert, 任务结束 -> done, 空闲 -> idle。
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

# 配色 (与 repl.C 一致的语义, 这里直接写 hex 供 SVG 使用)
_PALETTE = {
    IDLE:     {"body": "#2DD4BF", "dark": "#0F766E", "eye": "#0B3B38", "accent": "#5EEAD4"},
    THINKING: {"body": "#38BDF8", "dark": "#0369A1", "eye": "#0B2540", "accent": "#BAE6FD"},
    WORKING:  {"body": "#34D399", "dark": "#047857", "eye": "#053B2A", "accent": "#A7F3D0"},
    ALERT:    {"body": "#FB923C", "dark": "#C2410C", "eye": "#3B1A06", "accent": "#FED7AA"},
    DONE:     {"body": "#A78BFA", "dark": "#6D28D9", "eye": "#2A1A55", "accent": "#DDD6FE"},
}


class Mascot:
    """青小团吉祥物: 管理状态并渲染 ascii / svg 两种形态。"""

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
        """推进动画帧计数 (用于 ascii 旋转/浮动)。返回当前帧序号。"""
        self._frame = (self._frame + 1) % 4
        return self._frame

    @property
    def color(self) -> str:
        return self._flow_color(_PALETTE[self.state]["body"])

    def _flow_color(self, state_color: str) -> str:
        """在莫奈青与莫奈蓝之间缓慢呼吸，保留警告/完成状态的语义色。"""
        if self.state in (ALERT, DONE):
            return state_color
        teal = (45, 212, 191)
        blue = (59, 130, 246)
        phase = (math.sin((time.monotonic() - self._started_at) * 0.9) + 1) / 2
        # 状态色作为中间权重，让思考偏蓝、工作偏青。
        state_rgb = self._hex(state_color)
        weight = 0.25 + phase * 0.5
        rgb = tuple(round((1 - weight) * state_rgb[i] + weight * (blue[i] if self.state == THINKING else teal[i])) for i in range(3))
        return "#%02X%02X%02X" % rgb

    # ---------------------------------------------------------- ASCII 帧
    # 每个状态 4 帧, 用 ANSI 着色。富终端可见动画, 朴素终端退化为静态小图。
    _SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    _FLOAT = [" ", "·", ":", "·"]  # 浮动相位
    _THINK_Q = ["?", "°", "¿", "°"]

    def ascii(self, frame: int | None = None, color: bool = True) -> str:
        """返回 ANSI 着色的单行使者小图 (约 3 行高, 用 \\n 分隔)。

        适合嵌在 banner 左侧或状态栏。frame 缺省用内部计数。
        """
        f = self._frame if frame is None else (frame % 4)
        st = self.state
        if st == IDLE:
            body = self._ascii_idle(f)
        elif st == THINKING:
            body = self._ascii_thinking(f)
        elif st == WORKING:
            body = self._ascii_working(f)
        elif st == ALERT:
            body = self._ascii_alert(f)
        else:
            body = self._ascii_done(f)
        if color:
            return body
        # ASCII 图形内部的颜色包装只用于原始终端；Rich 渲染时去掉 ANSI，
        # 否则转义序列会被当作普通 banner 文本显示。
        import re
        return re.sub(r"\033\[[0-9;]*m", "", body)

    def _wrap(self, body: str, color_hex: str) -> str:
        # 用 24bit ANSI 着色 (大多数现代终端支持)
        r, g, b = self._hex(color_hex)
        return f"\033[38;2;{r};{g};{b}m{body}\033[0m"

    @staticmethod
    def _hex(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def _ascii_idle(self, f: int) -> str:
        c = self.color
        breath = self._FLOAT[f]
        body = f"  {breath}( ◡ ){breath}\n" \
               f"  ╭─────╮\n" \
               f"  ╰─────╯"
        return self._wrap(body, c)

    def _ascii_thinking(self, f: int) -> str:
        c = self.color
        q = self._THINK_Q[f]
        spin = self._SPIN[(f * 2) % len(self._SPIN)]
        body = f"   {q} {spin}\n" \
               f"  ( ◠ ◠ )\n" \
               f"  ╭─────╮\n" \
               f"  ╰─────╯"
        return self._wrap(body, c)

    def _ascii_working(self, f: int) -> str:
        c = self.color
        lift = self._FLOAT[f]
        body = f"  ( • • )\n" \
               f" {lift}╭─────╮{lift}\n" \
               f"  ╰─────╯\n" \
               f"   ▔▔▔▔▔"
        return self._wrap(body, c)

    def _ascii_alert(self, f: int) -> str:
        c = _PALETTE[ALERT]["body"]
        shake = " " if f % 2 == 0 else "!"
        body = f"  {shake}( @ • ){shake}\n" \
               f"  ╭─────╮\n" \
               f"  ╰─────╯\n" \
               f"   ⚠ 拦截"
        return self._wrap(body, c)

    def _ascii_done(self, f: int) -> str:
        c = _PALETTE[DONE]["body"]
        spark = "✦" if f % 2 == 0 else "  "
        body = f"  {spark}\n" \
               f"  ( ^ ^ )\n" \
               f"  ╭─────╮\n" \
               f"  ╰─────╯"
        return self._wrap(body, c)

    # ---------------------------------------------------------- SVG 动态
    def svg(self, size: int = 120) -> str:
        """返回自包含 SVG (含 SMIL 动画), 状态不同动画不同。

        用于富 UI / 文档 / README。viewBox 0 0 120 120。
        """
        p = _PALETTE[self.state]
        body, dark, eye, accent = p["body"], p["dark"], p["eye"], p["accent"]
        # 不同状态的核心动画段
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
    <!-- 团子身体 -->
    <ellipse cx="60" cy="68" rx="34" ry="32" fill="{body}" stroke="{dark}" stroke-width="3"/>
    <ellipse cx="60" cy="68" rx="34" ry="32" fill="{accent}" opacity="0.12"/>
    <!-- 眼睛 -->
    <circle cx="49" cy="64" r="5.5" fill="{eye}"/>
    <circle cx="71" cy="64" r="5.5" fill="{eye}"/>
    <!-- 腮红 -->
    <circle cx="44" cy="78" r="4" fill="{accent}" opacity="0.6"/>
    <circle cx="76" cy="78" r="4" fill="{accent}" opacity="0.6"/>
    <!-- 嘴: 随状态变化 -->
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
