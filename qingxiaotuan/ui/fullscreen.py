"""全屏终端工作台。

`FullScreenTUI` 提供状态栏、活动面板、事件日志和多行输入框。调用方通过
`on_submit` 注入 Agent 处理函数；函数可以同步返回文本，也可以返回 None 并自行
通过 `append_log` 更新进度。Ctrl+Enter 提交，Ctrl+L 清空日志，Ctrl+Q 退出。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.containers import ConditionalContainer
try:  # prompt_toolkit >= 3.0.50 把 ScrollablePane 移到了独立模块
    from prompt_toolkit.layout.scrollable_pane import ScrollablePane
except ImportError:  # 旧版本仍在 containers 里
    from prompt_toolkit.layout.containers import ScrollablePane
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea


class FullScreenTUI:
    """面向 Agent CLI 的全屏工作台。"""

    def __init__(self, on_submit: Callable[[str], Optional[str]], title: str = "青小团") -> None:
        self.on_submit = on_submit
        self.title = title
        self._status = "就绪"
        self._events: list[str] = []
        self._closed = False
        self._log_control = FormattedTextControl(self._render_events)
        self._status_control = FormattedTextControl(self._render_status)
        self.input = TextArea(
            prompt="  > ", multiline=True, wrap_lines=True,
            scrollbar=True, height=5,
        )
        self._app = Application(
            layout=Layout(self._build_layout(), focused_element=self.input),
            key_bindings=self._keys(),
            style=Style.from_dict({
                "status": "bg:#16324f #d9f0ff",
                "title": "bold #63d7c5",
                "panel": "#8aa4b8",
                "log": "#e8eef2",
                "input": "#f5f7f8",
                "bottom-toolbar": "#8aa4b8",
            }),
            full_screen=True,
            mouse_support=True,
        )

    def _build_layout(self):
        header = Window(self._status_control, height=1, style="class:status")
        side = Window(FormattedTextControl(self._render_side), width=24,
                      style="class:panel", wrap_lines=True)
        log = ScrollablePane(Window(self._log_control, wrap_lines=True), height=None)
        input_box = Window(BufferControl(self.input.buffer), height=5, style="class:input")
        footer = Window(FormattedTextControl(
            "Ctrl+Enter 提交 · Ctrl+L 清空日志 · Ctrl+Q 退出"), height=1,
            style="class:bottom-toolbar")
        return HSplit([header, VSplit([side, log]), input_box, footer])

    def _keys(self) -> KeyBindings:
        keys = KeyBindings()

        @keys.add("c-q")
        def _quit(event) -> None:
            self._closed = True
            event.app.exit()

        @keys.add("c-l")
        def _clear(event) -> None:
            self._events.clear()
            event.app.invalidate()

        @keys.add("c-enter")
        def _submit(event) -> None:
            text = self.input.text.strip()
            if not text:
                return
            self.input.buffer.reset()
            self._status = "处理中"
            self.append_log(f"> {text}")
            threading.Thread(target=self._run_submit, args=(text,),
                             name="qxt-tui-submit", daemon=True).start()

        return keys

    def _run_submit(self, text: str) -> None:
        try:
            result = self.on_submit(text)
            if result:
                self.append_log(result)
            self._status = "就绪"
        except Exception as exc:  # noqa: BLE001
            self._status = "错误"
            self.append_log(f"[错误] {type(exc).__name__}: {exc}")
        self._app.invalidate()

    def append_log(self, text: str) -> None:
        """追加一条事件日志，并通知终端刷新。"""
        self._events.append(str(text))
        if len(self._events) > 500:
            del self._events[:-500]
        if self._app.is_running:
            self._app.invalidate()

    def set_status(self, text: str) -> None:
        """更新顶部状态栏。"""
        self._status = text
        if self._app.is_running:
            self._app.invalidate()

    def run(self) -> None:
        """阻塞运行全屏应用，退出后释放终端控制权。"""
        self._app.run()

    def _render_status(self):
        return [("class:title", f"  {self.title}  "), ("", f"| {self._status}")]

    def _render_side(self):
        return [("class:panel", "活动\n\n"), ("", f"事件 {len(self._events)}\n"),
                ("", "输入区已聚焦\n"), ("", "Ctrl+Q 退出")]

    def _render_events(self):
        return "\n".join(self._events) or "等待任务输入…"
