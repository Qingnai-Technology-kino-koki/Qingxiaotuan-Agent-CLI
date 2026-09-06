"""即时 Splash TUI —— <50ms 弹出, 无 kernel 依赖。

核心思路:
  1. 用户敲 `qxt` 后 <50ms 内弹出 splash TUI (纯 prompt_toolkit, 零重依赖)
  2. 内核在后台线程构建 (~1s), splash 展示加载进度
  3. 内核就绪后无缝切换到正式 TUI, 用户无感
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Optional

_HAS_PT = False
try:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.output.base import DummyOutput
    _HAS_PT = True
except Exception:
    pass

_PRIMARY = "#4FA8FF"
_ACCENT = "#5BC0BE"
_DIM = "#888888"
_TEXT = "#E0E0E0"

_SPLASH_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_LOGO = [
    "  ▐█▛█▛█▌  青小团",
    "  ▐█████▌  Qingxiaotuan CLI",
]


class SplashTUI:
    """即时 Splash: 纯 prompt_toolkit, 无 kernel 依赖。

    Application 在 run() 时才创建 (延迟初始化), 避免 __init__ 阻塞。
    """

    def __init__(self) -> None:
        self._kernel_ready = threading.Event()
        self._kernel: Any = None
        self._error: Optional[str] = None
        self._frame = 0
        self._closed = False
        self._status = "初始化中..."
        self._progress = 0
        self._app: Optional[Application[Any]] = None
        self._log_control = None
        self._output = None

        if _HAS_PT:
            self._log_control = FormattedTextControl(self._render)
            try:
                if not getattr(sys.stdout, "isatty", lambda: False)():
                    self._output = DummyOutput()
            except Exception:
                pass

    def _ensure_app(self) -> None:
        """延迟创建 Application。"""
        if self._app is not None or not _HAS_PT or self._log_control is None:
            return
        try:
            kb = KeyBindings()

            @kb.add("c-c")
            def _(event):
                self._closed = True
                event.app.exit()

            @kb.add("c-q")
            def _(event):
                self._closed = True
                event.app.exit()

            layout = Layout(
                HSplit([Window(self._log_control, wrap_lines=True)])
            )
            # 注意: Style.from_dict 的 key 必须是『纯类名』, 不能带 class: 前缀
            # (带前缀会触发 AssertionError, 导致 Splash 静默降级为无 UI)。
            # 渲染时引用才写 "class:title" 这种形式。
            style = Style.from_dict({
                "title": "ansibrightblue",
                "dim": "ansibrightblack",
                "text": "ansiwhite",
                "accent": "ansibrightcyan",
            })
            self._app = Application(
                layout=layout, key_bindings=kb, style=style,
                full_screen=True, mouse_support=False, output=self._output,
            )
        except Exception:
            self._app = None

    def set_kernel(self, kernel: Any) -> None:
        self._kernel = kernel
        self._status = "就绪"
        self._progress = 100
        self._kernel_ready.set()
        # 内核就绪: 立刻退出 splash 事件循环, 让 fast_start 无缝切到正式 TUI。
        # (否则 run() 会一直阻塞在 app.run(), 主 TUI 永远启动不了,
        #  用户只能看到吉祥物 + 100% 进度条, 且无输入栏。)
        if self._app and self._app.is_running:
            self._app.invalidate()
            try:
                self._app.exit()
            except Exception:
                pass

    def set_error(self, error: str) -> None:
        self._error = error
        self._status = f"错误: {error}"
        self._kernel_ready.set()
        if self._app and self._app.is_running:
            self._app.invalidate()
            try:
                self._app.exit()
            except Exception:
                pass

    def set_progress(self, pct: int, status: str = "") -> None:
        self._progress = min(100, max(0, pct))
        if status:
            self._status = status
        if self._app and self._app.is_running:
            self._app.invalidate()

    @property
    def kernel(self) -> Any:
        return self._kernel

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def is_ready(self) -> bool:
        return self._kernel_ready.is_set()

    @property
    def has_error(self) -> bool:
        return self._error is not None

    def start(self) -> None:
        pass

    def run(self) -> None:
        self._ensure_app()
        if not _HAS_PT or self._app is None:
            self._run_fallback()
            return
        # 竞态: 若 kernel 在 app 启动前就已就绪, 直接跳过 splash, 让 fast_start 启动主 TUI。
        if self._kernel_ready.is_set():
            self._closed = True
            return
        animator = threading.Thread(target=self._animate, daemon=True)
        animator.start()
        try:
            self._app.run()
        finally:
            self._closed = True

    def close(self) -> None:
        self._closed = True
        if self._app and self._app.is_running:
            self._app.exit()

    def _animate(self) -> None:
        while not self._closed and not self._kernel_ready.is_set():
            self._frame = (self._frame + 1) % len(_SPLASH_FRAMES)
            if self._app and self._app.is_running:
                self._app.invalidate()
            time.sleep(0.08)

    def _render(self) -> list:
        frame = _SPLASH_FRAMES[self._frame % len(_SPLASH_FRAMES)]
        lines = [("", "\n\n")]
        for logo_line in _LOGO:
            lines.append(("class:title", f"  {logo_line}\n"))
        lines.append(("", "\n"))
        if self._error:
            lines.append(("class:text", f"  ✗ {self._error}\n"))
        elif self._kernel_ready.is_set():
            lines.append(("class:accent", "  ✓ 就绪\n"))
        else:
            lines.append(("class:text", f"  {frame} {self._status}\n"))
        bar_len = 30
        filled = int(self._progress / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        lines.append(("class:dim", f"  [{bar}] {self._progress}%\n"))
        lines.append(("", "\n"))
        lines.append(("class:dim", "  Ctrl-C 退出\n"))
        return lines

    def _run_fallback(self) -> None:
        print("青小团 Qingxiaotuan CLI")
        print("加载中...")
        self._kernel_ready.wait(timeout=30)
        if self._error:
            print(f"错误: {self._error}")
        else:
            print("就绪!")
