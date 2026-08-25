"""UI - DeepSeek style terminal workspace."""

from __future__ import annotations

from pathlib import Path
import os
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from .mascot import Mascot, IDLE
from .theme import C, MASCOT_ICONS, context_bar, context_style

_HAS_PT = False
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    _HAS_PT = True
except Exception:
    _HAS_PT = False


def _banner_lines(cwd: str, session: str, model: str, version: str) -> list:
    return [
        "",
        "  ▐█▛█▛█▌  Welcome to 青小团 CLI!",
        "           Run /help to get started.",
        "",
        f"  Directory: {cwd}",
        f"  Session:   {session}",
        f"  Model:     {model}",
        f"  Version:   {version}",
        "",
    ]


class UI:
    """Terminal UI with real prompt_toolkit input."""

    def __init__(self, console: Optional[Console] = None, home: Optional[Path] = None) -> None:
        self.console = console or Console()
        self.home = Path(home) if home else Path.home() / ".qingxiaotuan"
        self._session: Optional["PromptSession"] = None
        self._pt_disabled = not _HAS_PT
        self._cwd = os.getcwd()
        self._session_name = "No session yet"
        self._model = "not set, run /login or /provider"
        self._version = "0.01"
        self._context_pct = 0.0
        self._ctx_pct = 0.0
        self._branch = "main"
        self._tokens = 0
        self._mascot = Mascot(IDLE)
        # 长输出折叠: 最近一次被折叠的内容, 供 /more 展开
        self._last_collapsed: Optional[str] = None

    def _detect_branch(self) -> str:
        try:
            import subprocess
            result = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=2)
            return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "main"
        except Exception:
            return "main"

    def render_banner(self) -> None:
        lines = _banner_lines(self._cwd, self._session_name, self._model, self._version)
        console = self.console
        width = console.width or 100
        border = "-" * max(1, width - 2)
        console.print(Text("+" + border + "+", style=C["box"]))
        for line in lines:
            pad = max(1, width - len(line) - 2)
            console.print(Text("|" + line + " " * pad + "|", style=C["box"]))
        console.print(Text("+" + border + "+", style=C["box"]))
        console.print()

    def banner(self, config=None, workspace: str = "", model_label: str = "", profile: str = "",
               mode: str = "", effort: str = "") -> None:
        self._cwd = workspace or os.getcwd()
        self._branch = self._detect_branch()
        self._model = model_label or self._model
        # 从 config 获取额外信息
        if config is not None:
            if hasattr(config, "get"):
                self._session_name = config.get("session_id", self._session_name) or self._session_name
        self.render_banner()
        # 底部状态栏
        self.status_bar(mode, effort, workspace)

    def status_bar(self, mode: str = "", effort: str = "", workspace: str = "",
                   plan: bool = False) -> None:
        self._cwd = workspace or self._cwd
        self._branch = self._detect_branch()
        console = self.console
        width = console.width or 100
        # 状态图标 (动态吉祥物状态)
        icon = MASCOT_ICONS.get(self._mascot.state, "◦")
        left = f"{icon} {self._cwd}  {self._branch}"
        if plan:
            left += "  [PLAN]"
        parts = []
        if self._tokens:
            parts.append(f"tok={self._tokens}")
        if self._ctx_pct is not None:
            parts.append(f"ctx={self._ctx_pct:.0f}%")
        right = " | ".join(parts) if parts else ""
        sep = " · " if right else ""
        line = f"  {left}{sep}{right}"
        console.print(Text(line, style=C["muted"]))

    def _build_session(self) -> Optional["PromptSession"]:
        if self._pt_disabled:
            return None
        try:
            hist = self.home / "history" / "repl.txt"
            hist.parent.mkdir(parents=True, exist_ok=True)
            kb = KeyBindings()

            @kb.add("enter")
            def _(event):
                event.current_buffer.validate_and_handle()

            @kb.add("escape", "enter")
            def _(event):
                event.current_buffer.insert_text("\n")

            @kb.add("c-c")
            def _(event):
                if event.current_buffer.text:
                    event.current_buffer.reset()
                else:
                    event.current_buffer.validate_and_handle()

            return PromptSession(
                history=FileHistory(str(hist)),
                key_bindings=kb,
                multiline=True,
                prompt_continuation="  ",
                bottom_toolbar="[dim]Enter send | Esc+Enter newline | Ctrl+C clear | /help[/]",
                enable_history_search=True,
            )
        except Exception:
            self._pt_disabled = True
            return None

    def prompt(self, prefix: str = "  > ") -> str:
        if self._session is None:
            self._session = self._build_session()
        if self._session is None:
            try:
                return input(prefix).strip()
            except (EOFError, KeyboardInterrupt):
                raise
        try:
            text = self._session.prompt(prefix)
        except KeyboardInterrupt:
            raise
        return text.strip()

    def info(self, text: str) -> None:
        self.console.print(Text(f"  {text}", style=C["text"]))

    def error(self, text: str) -> None:
        self.console.print(Text(f"  x {text}", style=C["err"]))

    def success(self, text: str) -> None:
        self.console.print(Text(f"  v {text}", style=C["ok"]))

    def think_start(self) -> None:
        self.console.print(Text("  ... thinking ...", style=C["dim"]))

    def stream(self, token: str) -> None:
        self.console.print(token, end="", style=C["text"])

    def reason(self, text: str) -> None:
        self.console.print(Text(f"  ? {text}", style=C["dim"]))

    def tool_call(self, name: str, args: dict) -> None:
        self.console.print(Text(f"  tool {name} {str(args)[:120]}", style=C["warn"]))

    def tool_result(self, name: str, result: str) -> None:
        failed = any(k in result for k in ("[错误]", "[已拒绝]", "Error", "error:", "Traceback", "exit=1", "失败"))
        icon = "✗" if failed else "✓"
        # 长输出折叠: 超长结果只显示首尾 + 中段统计, 避免刷屏
        if len(result) > self._COLLAPSE_THRESHOLD and not failed:
            self._print_collapsed(result, icon, name)
            return
        flat = result.replace("\n", " ").strip()
        s = flat[:160] + f" …({len(result)}字符)" if len(flat) > 160 else flat
        self.console.print(Text(f"  └─ {icon} {s}", style=C["dim"]), highlight=False)

    _COLLAPSE_THRESHOLD = 600  # 超过该字符数即折叠中段

    def _print_collapsed(self, text: str, icon: str, name: str = "") -> None:
        """折叠式长输出: 头 8 行 + 中段统计 + 尾 4 行, 提示可用 /more 展开。"""
        self._last_collapsed = text  # 供 /more 展开
        lines = text.split("\n")
        head_n, tail_n = 8, 4
        if len(lines) <= head_n + tail_n + 2:
            # 行数不多但字符多 (如单行巨长), 退化显示首尾各 240 字符
            head = text[:240]
            tail = text[-240:] if len(text) > 240 else ""
            self.console.print(
                Text(f"  └─ {icon} {head!r} … (共 {len(text)} 字符) … {tail!r}", style=C["dim"]))
            return
        head = "\n".join(lines[:head_n])
        tail = "\n".join(lines[-tail_n:])
        mid_lines = len(lines) - head_n - tail_n
        mid_chars = sum(len(l) for l in lines[head_n:-tail_n])
        self.console.print(Text(f"  └─ {icon} {head}", style=C["dim"]))
        self.console.print(
            Text(f"  │   … 中段已折叠: {mid_lines} 行 / {mid_chars} 字符 (输入 /more 展开全文) …",
                 style=C["muted"]))
        self.console.print(Text(f"  └─ {tail}", style=C["dim"]))

    def show_more(self) -> None:
        """展开最近一次被折叠的长输出。"""
        if self._last_collapsed:
            self.console.print(Text("  ── 展开全文 ──", style=C["accent"]))
            self.console.print(self._last_collapsed)
            self.console.print(Text("  ── 折叠结束 ──", style=C["accent"]))
            self._last_collapsed = None

    def answer_md(self, text: str) -> None:
        # 超长回答也折叠
        if len(text) > self._COLLAPSE_THRESHOLD:
            self._print_collapsed(text, "✦", "answer")
        else:
            self.console.print(Markdown(text))

    def add_tokens(self, n: int) -> None:
        self._tokens += max(0, int(n))

    def set_context_pct(self, pct: float) -> None:
        self._ctx_pct = max(0.0, min(100.0, float(pct)))

    def context_bar(self, info: dict) -> None:
        """上下文占用条: 按 estimated/budget 计算百分比并着色 (高占用变红)。"""
        est = info.get("estimated_tokens", 0)
        budget = info.get("budget_tokens", 0) or 1
        pct = max(0.0, min(100.0, est / budget * 100.0))
        self._ctx_pct = pct
        bar = context_bar(pct)
        style = context_style(pct)
        self.console.print(Text(f"  上下文 {pct:.0f}% [{bar}]", style=C[style]))

    def usage(self, usage: dict) -> None:
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        self.console.print(Text(f"  usage: {pt} in / {ct} out tokens", style=C["muted"]))

    def mascot_set(self, state: str) -> None:
        self._mascot.set(state)
        state_icon = {"idle": "(o)", "thinking": "? ...", "working": "(* *)", "alert": "(!)", "done": "OK"}
        self.console.print(Text(f"  {state_icon.get(state, state)}", style=C["accent"]))

    # ------------------------------------------------------------ 快捷键面板

    _KEY_GROUPS = (
        ("输入", (
            ("Enter", "发送当前输入"),
            ("Esc + Enter", "插入换行 (多行输入)"),
            ("↑ / ↓", "浏览历史 (开启历史搜索)"),
            ("Ctrl + C", "中断 / 取消当前输入"),
            ("Ctrl + L", "清屏"),
            ("Ctrl + G", "打开本命令面板"),
        )),
        ("常用斜杠命令", (
            ("/help", "显示帮助"),
            ("/model", "切换/查看模型"),
            ("/effort", "切换推理投入 low/medium/high"),
            ("/plan", "切换 Plan 模式 (只读分析)"),
            ("/resume", "恢复历史会话"),
            ("/usage", "本次会话 token 用量"),
            ("/context", "上下文占用"),
            ("/more", "展开上一条折叠的长输出"),
            ("/loop", "进入自主开发循环"),
            ("/review", "自动代码评审"),
            ("/clear", "清空对话上下文"),
            ("/exit", "退出"),
        )),
    )

    def keymap_panel(self) -> Optional[str]:
        """纯键盘快捷键 / 命令面板。"""
        try:
            from prompt_toolkit.widgets import RadioList
            from prompt_toolkit import Application
            from prompt_toolkit.layout.containers import HSplit, Window
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.layout.dimension import D
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.filters import Condition
        except Exception:
            self._print_keymap_fallback()
            return None

        try:
            return self._build_keymap_app(RadioList, Application, HSplit, Window,
                                          FormattedTextControl, D, KeyBindings, Condition)
        except Exception:
            self._print_keymap_fallback()
            return None

    def _build_keymap_app(self, RadioList, Application, HSplit, Window,
                          FormattedTextControl, D, KeyBindings, Condition) -> Optional[str]:
        options = []
        for grp_name, items in self._KEY_GROUPS:
            for key, desc in items:
                fill = key if key.startswith("/") else ""
                label = f"{key:<12} {desc}"
                options.append((fill, label))
        seen = set()
        uniq = []
        for fill, label in options:
            if label in seen:
                continue
            seen.add(label)
            uniq.append((fill, label))

        radio = RadioList(values=uniq)
        title_text = "青小团 · 快捷键 / 命令面板   (↑↓ 选择 · Enter 执行斜杠命令 · Esc 取消)"

        @Condition
        def is_radiolist_selected():
            return True

        kb = KeyBindings()

        @kb.add("escape")
        def _(event):
            event.app.exit(result=None)

        @kb.add("enter")
        def _(event):
            fill = radio.current_value
            event.app.exit(result=fill if fill else None)

        layout = HSplit([
            Window(FormattedTextControl(title_text, focusable=False), height=D(min=1, max=1)),
            radio,
        ])
        app = Application(layout=layout, key_bindings=kb, full_screen=False,
                          mouse_support=False)
        try:
            return app.run()
        except Exception:
            self._print_keymap_fallback()
            return None

    def _print_keymap_fallback(self) -> None:
        """prompt_toolkit 组件不可用时的降级: 直接打印键位表。"""
        for grp_name, items in self._KEY_GROUPS:
            self.console.print(Text(f"  {grp_name}", style=C["accent"]))
            for key, desc in items:
                self.console.print(Text(f"    {key:<12} {desc}", style=C["dim"]))
