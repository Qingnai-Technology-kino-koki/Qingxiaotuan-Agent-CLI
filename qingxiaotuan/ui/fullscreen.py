"""全屏终端工作台。

`FullScreenTUI` 提供状态栏、活动面板、事件日志和多行输入框。调用方通过
`on_submit` 注入 Agent 处理函数；函数可以同步返回文本，也可以返回 None 并自行
通过 `append_log` 更新进度。Ctrl+Enter 提交，Ctrl+L 清空日志，Ctrl+Q 退出。
Tab 键切换焦点（侧栏/日志区/输入区）。
"""

from __future__ import annotations

import threading
import time
import os
import sys
from typing import Callable, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.output.base import DummyOutput
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
from .mascot import Mascot, IDLE
from .theme import PT_STYLE, MASCOT_ICONS, context_bar, context_style


class FullScreenTUI:
    """面向 Agent CLI 的全屏工作台。"""

    def __init__(self, on_submit: Callable[[str], Optional[str]], title: str = "青小团",
                 on_cancel: Optional[Callable[[], None]] = None,
                 on_command: Optional[Callable[[str], Optional[str]]] = None) -> None:
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.on_command = on_command
        self.title = title
        self._status = "就绪"
        self._events: list[str] = []
        self._closed = False
        self._busy = False
        self._cancel_requested = threading.Event()
        self._task_lock = threading.Lock()
        self._mascot_frame = 0
        self._mascot_state = "idle"
        self._mascot = Mascot(IDLE)
        self._tokens = 0
        self._context_pct: Optional[float] = None
        self._context_info: Optional[dict] = None  # {"estimated_tokens":.., "budget_tokens":..}
        self._plan_mode = False  # Plan 模式: 只读, 状态栏/侧栏显示 [PLAN]
        self._focus_index = 0  # 0=侧栏, 1=日志, 2=输入区
        output = None
        # prompt_toolkit 在 Windows 无控制台时会直接创建 Win32Output 并抛异常；
        # 测试、CI 和非 TTY 调用使用 DummyOutput，真实终端仍走默认输出。
        try:
            is_test_or_ci = bool(
                os.environ.get("PYTEST_CURRENT_TEST")
                or os.environ.get("PYTEST_VERSION")
                or os.environ.get("CI")
                or "pytest" in sys.modules
            )
            if is_test_or_ci or not getattr(sys.stdout, "isatty", lambda: False)():
                output = DummyOutput()
        except Exception:
            output = DummyOutput()
        self._log_control = FormattedTextControl(self._render_events)
        self._status_control = FormattedTextControl(self._render_status)
        self.input = TextArea(
            prompt="  > ", multiline=True, wrap_lines=True,
            scrollbar=True, height=5,
        )
        self._app_kwargs = {
            "layout": Layout(self._build_layout()),
            "key_bindings": self._keys(),
            "style": Style.from_dict(PT_STYLE),
            "full_screen": True,
            "mouse_support": True,
            "output": output,
        }
        try:
            self._app = Application(**self._app_kwargs)
        except Exception as exc:
            # 某些 Windows 捕获器错误地报告 isatty=True，但没有控制台缓冲区。
            # 只有输出初始化失败时回退，不能吞掉布局或快捷键的真正错误。
            if type(exc).__name__ != "NoConsoleScreenBufferError":
                raise
            self._app_kwargs["output"] = DummyOutput()
            self._app = Application(**self._app_kwargs)

    def _build_layout(self):
        header = Window(self._status_control, height=1, style="class:status")
        side = Window(FormattedTextControl(self._render_side), width=24,
                      style="class:panel", wrap_lines=True)
        log = ScrollablePane(Window(self._log_control, wrap_lines=True), height=None)
        self.input.window.style = "class:input"
        input_box = self.input
        footer = Window(FormattedTextControl(
            "Tab 切换焦点 · Ctrl+J 提交 · Ctrl+L 清空日志 · Ctrl+C 取消 · Ctrl+Q 退出"), height=1,
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

        @keys.add("tab")
        def _toggle_focus(event) -> None:
            self._cycle_focus()
            event.app.invalidate()

        @keys.add("c-c")
        def _cancel(event) -> None:
            if not self._busy:
                self.append_log("没有正在运行的任务")
                return
            self._cancel_requested.set()
            self._status = "取消中"
            self._mascot_state = "alert"
            if self.on_cancel:
                self.on_cancel()
            self.append_log("已请求取消当前任务")
            event.app.invalidate()

        @keys.add("enter")
        def _submit(event) -> None:
            text = self.input.text.strip()
            if not text:
                return
            if self._busy:
                self.append_log("当前任务仍在运行，请先 Ctrl+C 取消")
                return
            self.input.buffer.reset()
            self._status = "处理中"
            self._mascot_state = "thinking"
            self.append_log(f"> {text}")
            if text.startswith("/") and self.on_command:
                result = self.on_command(text)
                if result:
                    self.append_log(result)
                self._status = "就绪"
                self._mascot_state = "idle"
                return
            self._busy = True
            self._cancel_requested.clear()
            threading.Thread(target=self._run_submit, args=(text,),
                             name="qxt-tui-submit", daemon=True).start()

        return keys

    def _run_submit(self, text: str) -> None:
        try:
            result = self.on_submit(text)
            if result:
                self.append_log(result)
            self._status = "已取消" if self._cancel_requested.is_set() else "就绪"
            self._mascot_state = "alert" if self._cancel_requested.is_set() else "done"
        except Exception as exc:  # noqa: BLE001
            self._status = "错误"
            self._mascot_state = "alert"
            self.append_log(f"[错误] {type(exc).__name__}: {exc}")
        finally:
            self._busy = False
        self._app.invalidate()

    def _cycle_focus(self) -> None:
        """Tab 键循环切换焦点：侧栏 → 日志 → 输入区 → 侧栏。"""
        self._focus_index = (self._focus_index + 1) % 3
        self.append_log(
            f"[焦点] 切换到 {'侧栏' if self._focus_index == 0 else '日志' if self._focus_index == 1 else '输入区'}"
        )

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

    def set_mascot(self, state: str) -> None:
        """设置吉祥物状态：idle、thinking、working、alert 或 done。"""
        if state in {"idle", "thinking", "working", "alert", "done"}:
            self._mascot_state = state
            self._mascot.set(state)
            if self._app.is_running:
                self._app.invalidate()

    def add_tokens(self, count: int) -> None:
        """增加会话 token 计数，供 CLI 回调更新状态栏。"""
        self._tokens += max(0, int(count))
        if self._app.is_running:
            self._app.invalidate()

    def set_context_pct(self, percent: float) -> None:
        """设置上下文占用百分比（0 到 100）。"""
        self._context_pct = max(0.0, min(100.0, float(percent)))
        if self._app.is_running:
            self._app.invalidate()

    def set_plan_mode(self, enabled: bool) -> None:
        """设置 Plan 模式开关, 状态栏/侧栏显示 [PLAN] 指示。"""
        self._plan_mode = bool(enabled)
        if self._app.is_running:
            self._app.invalidate()

    def set_context_info(self, estimated: int, budget: int) -> None:
        """设置上下文估算/预算 token, 侧栏实时渲染占用条。"""
        self._context_info = {"estimated_tokens": int(estimated), "budget_tokens": int(budget)}
        budget = budget or 1
        self._context_pct = max(0.0, min(100.0, estimated / budget * 100.0))
        if self._app.is_running:
            self._app.invalidate()

    def context_bar(self, info: dict) -> None:
        """与 repl.UI.context_bar 同签名: 按 estimated/budget 更新占用条。"""
        self.set_context_info(info.get("estimated_tokens", 0), info.get("budget_tokens", 0) or 1)

    def run(self) -> None:
        """阻塞运行全屏应用，退出后释放终端控制权。"""
        animator = threading.Thread(target=self._animate, name="qxt-tui-mascot", daemon=True)
        animator.start()
        try:
            self._app.run()
        finally:
            self._closed = True

    def close(self) -> None:
        """请求关闭全屏应用并停止动画刷新。"""
        self._closed = True
        if self._app.is_running:
            self._app.exit()

    def _animate(self) -> None:
        while not self._closed:
            time.sleep(0.28)
            self._mascot_frame = (self._mascot_frame + 1) % 4
            if self._app.is_running:
                self._app.invalidate()

    def _render_status(self):
        icon = MASCOT_ICONS.get(self._mascot_state, "◦")
        parts = [f"{icon} {self.title}", self._status]
        if self._plan_mode:
            parts.append("[PLAN]")
        if self._tokens:
            parts.append(f"tok={self._tokens}")
        if self._context_pct is not None:
            parts.append(f"ctx={self._context_pct:.0f}%")
        return [("class:title", "  " + "  |  ".join(parts))]

    def _render_side(self):
        state = "运行中" if self._busy else "空闲"
        self._mascot.set(self._mascot_state)
        art = self._mascot.ascii(self._mascot_frame, color=False)
        # working 状态用额外帧制造弹跳；其它状态仍由 Mascot 帧驱动表情变化。
        if self._mascot_state == "working" and self._mascot_frame % 2:
            art = art.replace("╭─────╮", "╭─────╮ ↑", 1)
        focus = ["●", "○", "○"][self._focus_index] if hasattr(self, "_focus_index") else "○"
        lines = [("class:panel", art + "\n\n"), ("", f"状态 {state}\n"),
                 ("", f"事件 {len(self._events)}\n"), ("", f"焦点 {focus}\n")]
        if self._plan_mode:
            lines.append(("class:panel", "模式 PLAN (只读)\n"))
        if self._context_pct is not None:
            bar = context_bar(self._context_pct)
            style = "context-" + context_style(self._context_pct)
            lines.append(("class:panel", f"上下文 {self._context_pct:.0f}% [{bar}]\n"))
            if self._context_info:
                est = self._context_info["estimated_tokens"]
                budget = self._context_info["budget_tokens"]
                lines.append(("", f"tok {est:,} / {budget:,}\n"))
        lines.append(("", "Tab 切换 · Ctrl+J 提交\nCtrl+C 取消 · Ctrl+Q 退出"))
        return lines

    def _render_events(self):
        return "\n".join(self._events) or "等待任务输入…"
