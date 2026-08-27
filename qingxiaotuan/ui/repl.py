"""UI - DeepSeek style terminal workspace."""

from __future__ import annotations

from pathlib import Path
import logging
import os
import unicodedata
from typing import Optional, cast

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from .mascot import Mascot, IDLE
from .plain_console import console as _plain_console
from .theme import C, MASCOT_ICONS, blank_pt_style, context_bar, context_style
from ..i18n import t

_HAS_PT = False
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    _HAS_PT = True
except Exception:
    _HAS_PT = False

log = logging.getLogger(__name__)

# 输入栏可补全的斜杠命令 (与 cli.commands._handle_slash 支持的命令保持一致)
_SLASH_COMMANDS = [
    "/help", "/tools", "/skills", "/memory", "/usage", "/cost", "/compact",
    "/context", "/clear", "/more", "/model", "/effort", "/mode", "/plan",
    "/resume", "/swarm", "/route", "/diff", "/undo", "/impact", "/mcp",
    "/hooks", "/image", "/images", "/clear-images", "/exit", "/quit",
]


def _disp_width(s: str) -> int:
    """字符串的终端显示宽度: 全角/宽字符按 2 列计 (CJK、块字符等)。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _fmt_tokens(n: int) -> str:
    """token 数的人性化显示: 128000 -> 128k, 1500000 -> 1.5M。"""
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _banner_lines(cwd: str, session: str, model: str, version: str) -> list:
    """qingxiaotuan 式启动横幅: 块状 logo + 欢迎 + Directory/Session/Model/Version 标签。

    标签宽度按当前语言动态对齐 (翻译后长度不一)。
    """
    labels = [t("banner.directory"), t("banner.session"),
              t("banner.model"), t("banner.version")]
    w = max(len(label) for label in labels)
    return [
        "",
        "  ▐█▛█▛█▌  " + t("banner.welcome"),
        "  ▐█████▌  " + t("banner.help_hint"),
        "",
        f"  {(labels[0] + ':').ljust(w + 1)} {cwd}",
        f"  {(labels[1] + ':').ljust(w + 1)} {session}",
        f"  {(labels[2] + ':').ljust(w + 1)} {model}",
        f"  {(labels[3] + ':').ljust(w + 1)} {version}",
        "",
    ]


def _tip_lines(session: str = "No session yet") -> list:
    """横幅下方的提示区 (对标 qingxiaotuan 的 Web UI 引导)。"""
    lines = [
        "",
        "  " + t("tip.fullscreen"),
        "    " + t("tip.fullscreen_cmd"),
    ]
    if not session or session == "No session yet":
        lines.append("")
        lines.append("    " + t("tip.no_session"))
    lines.append("")
    return lines


class UI:
    """Terminal UI with real prompt_toolkit input."""

    def __init__(self, console: Optional[Console] = None, home: Optional[Path] = None) -> None:
        self.console = console or _plain_console
        self.home = Path(home) if home else Path.home() / ".qingxiaotuan"
        self._session: Optional["PromptSession"] = None
        self._pt_disabled = not _HAS_PT
        self._cwd = os.getcwd()
        self._session_name = "No session yet"
        self._model = "not set, run /login or /provider"
        self._version = "0.01"
        self._context_pct = 0.0
        self._ctx_pct = 0.0
        self._ctx_used = 0
        self._ctx_budget = 0
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
        except Exception as exc:  # noqa: BLE001
            log.debug("git 分支探测失败, 回退 main: %s", exc)
            return "main"

    def _detect_dirty(self) -> bool:
        """git 工作区是否有未提交改动 (对标 qingxiaotuan 的 [±] 标记)。"""
        try:
            import subprocess
            result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=2)
            return bool(result.stdout.strip())
        except Exception as exc:  # noqa: BLE001
            log.debug("git 工作区状态探测失败, 视为干净: %s", exc)
            return False

    def render_banner(self) -> None:
        lines = _banner_lines(self._cwd, self._session_name, self._model, self._version)
        console = self.console
        width = console.width or 100
        inner = max(1, width - 2)
        console.print(Text("╭" + "─" * inner + "╮", style=C["box"]))
        for i, line in enumerate(lines):
            pad = max(1, width - _disp_width(line) - 2)
            # logo 与欢迎行用主强调色, 其余保持边框色 (视觉层次对标 qingxiaotuan)
            style = C["primary"] if i in (1, 2) else C["box"]
            console.print(Text("│" + line + " " * pad + "│", style=style))
        console.print(Text("╰" + "─" * inner + "╯", style=C["box"]))
        for line in _tip_lines(self._session_name):
            console.print(Text(line, style=C["dim"]))

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
        """qingxiaotuan 风格双行状态栏:
        左: model  cwd  branch[±]  右: /help: show commands
        下行右对齐: context: pct% (used/budget)
        """
        self._cwd = workspace or self._cwd
        self._branch = self._detect_branch()
        dirty = self._detect_dirty()
        console = self.console
        width = console.width or 100

        # 第一行: 左 吉祥物/model/cwd/branch, 右 /help 提示
        icon = MASCOT_ICONS.get(self._mascot.state, "◦")
        branch = f"{self._branch} [±]" if dirty else self._branch
        left = f"{icon} {self._model}  {self._cwd}  {branch}"
        if plan:
            left += "  [PLAN]"
        right = t("repl.help_hint")
        gap = max(1, width - _disp_width(left) - _disp_width(right))
        line1 = left + " " * gap + right
        console.print(Text(line1, style=C["muted"]))

        # 第二行: 右对齐 context 占用
        if self._ctx_budget:
            ctx = f"context: {self._ctx_pct:.0f}% ({_fmt_tokens(self._ctx_used)}/{_fmt_tokens(self._ctx_budget)})"
        elif self._ctx_pct:
            ctx = f"context: {self._ctx_pct:.0f}%"
        else:
            ctx = ""
        if ctx:
            console.print(Text(" " * max(1, width - _disp_width(ctx)) + ctx, style=C["muted"]))

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
                prompt_continuation="│ ",
                bottom_toolbar=t('repl.toolbar'),
                enable_history_search=True,
                style=Style.from_dict(blank_pt_style()),
                completer=WordCompleter(
                    _SLASH_COMMANDS,
                    ignore_case=True,
                    match_middle=True,
                    sentence=True,
                ),
                complete_while_typing=True,
            )
        except Exception:
            self._pt_disabled = True
            return None

    def prompt(self, prefix: str = "│ > ") -> str:
        """qingxiaotuan 风格框式输入: 顶部/底部圆角边框, 输入行带 │ 左框。"""
        console = self.console
        width = console.width or 100
        inner = max(1, width - 2)
        console.print(Text("╭" + "─" * inner + "╮", style=C["box"]))
        if self._session is None:
            self._session = self._build_session()
        if self._session is None:
            try:
                text = input(prefix).strip()
            except (EOFError, KeyboardInterrupt):
                raise
        else:
            try:
                text = self._session.prompt(FormattedText([("class:prompt", prefix)]))
            except KeyboardInterrupt:
                raise
        console.print(Text("╰" + "─" * inner + "╯", style=C["box"]))
        return text.strip()

    def info(self, text: str) -> None:
        self.console.print(Text(f"  {text}", style=C["text"]))

    def error(self, text: str) -> None:
        self.console.print(Text(f"  x {text}", style=C["err"]))

    def success(self, text: str) -> None:
        self.console.print(Text(f"  v {text}", style=C["ok"]))

    def think_start(self) -> None:
        self.console.print(Text(f"  {t('repl.thinking')}", style=C["dim"]))

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
        s = flat[:160] + t("repl.fold_chars", n=len(result)) if len(flat) > 160 else flat
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
                Text(f"  └─ {icon} {head!r} … {t('repl.fold_total', n=len(text))} … {tail!r}", style=C["dim"]))
            return
        head = "\n".join(lines[:head_n])
        tail = "\n".join(lines[-tail_n:])
        mid_lines = len(lines) - head_n - tail_n
        mid_chars = sum(len(l) for l in lines[head_n:-tail_n])
        self.console.print(Text(f"  └─ {icon} {head}", style=C["dim"]))
        self.console.print(
            Text("  │   " + t("repl.fold_middle", lines=mid_lines, chars=mid_chars),
                 style=C["muted"]))
        self.console.print(Text(f"  └─ {tail}", style=C["dim"]))

    def show_more(self) -> None:
        """展开最近一次被折叠的长输出。"""
        if self._last_collapsed:
            self.console.print(Text(f"  {t('repl.more_expand')}", style=C["accent"]))
            self.console.print(self._last_collapsed)
            self.console.print(Text(f"  {t('repl.more_end')}", style=C["accent"]))
            self._last_collapsed = None

    def answer_md(self, text: str) -> None:
        # 超长回答也折叠
        if len(text) > self._COLLAPSE_THRESHOLD:
            self._print_collapsed(text, "✦", "answer")
        else:
            self.console.print(Markdown(text))

    def add_tokens(self, n: int) -> None:
        self._tokens += max(0, int(n))

    def set_context_pct(self, pct: float, used: int = 0, budget: int = 0) -> None:
        self._ctx_pct = max(0.0, min(100.0, float(pct)))
        self._ctx_used = int(used)
        self._ctx_budget = int(budget)

    def context_bar(self, info: dict) -> None:
        """上下文占用条: 按 estimated/budget 计算百分比并着色 (高占用变红)。"""
        est = info.get("estimated_tokens", 0)
        budget = info.get("budget_tokens", 0) or 1
        pct = max(0.0, min(100.0, est / budget * 100.0))
        self._ctx_pct = pct
        self._ctx_used = int(est)
        self._ctx_budget = int(budget)
        bar = context_bar(pct)
        style = context_style(pct)
        self.console.print(Text(f"  {t('repl.context_bar', pct=f'{pct:.0f}', bar=bar)}",
                                style=C[style]))

    def usage(self, usage: dict) -> None:
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        self.console.print(Text(f"  {t('repl.usage', pt=pt, ct=ct)}", style=C["muted"]))

    def mascot_set(self, state: str) -> None:
        self._mascot.set(state)
        state_icon = {"idle": "(o)", "thinking": "? ...", "working": "(* *)", "alert": "(!)", "done": "OK"}
        self.console.print(Text(f"  {state_icon.get(state, state)}", style=C["accent"]))

    # ------------------------------------------------------------ 快捷键面板

    def _key_groups(self):
        """快捷键 / 斜杠命令面板条目 (描述走 i18n, 键名保持原样)。"""
        return (
            (t("keymap.group.input"), (
                ("Enter", t("keys.enter")),
                ("Esc + Enter", t("keys.esc_enter")),
                ("↑ / ↓", t("keys.arrows")),
                ("Ctrl + C", t("keys.ctrl_c")),
                ("Ctrl + L", t("keys.ctrl_l")),
                ("Ctrl + G", t("keys.ctrl_g")),
            )),
            (t("keymap.group.commands"), (
                ("/help", t("cmd.help")),
                ("/model", t("cmd.model")),
                ("/effort", t("cmd.effort")),
                ("/plan", t("cmd.plan")),
                ("/resume", t("cmd.resume")),
                ("/swarm", t("cmd.swarm")),
                ("/usage", t("cmd.usage")),
                ("/context", t("cmd.context")),
                ("/more", t("cmd.more")),
                ("/loop", t("cmd.loop")),
                ("/review", t("cmd.review")),
                ("/clear", t("cmd.clear")),
                ("/exit", t("cmd.exit")),
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
        for grp_name, items in self._key_groups():
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
        title_text = t("keymap.title")

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
                          mouse_support=False, style=Style.from_dict(blank_pt_style()))
        try:
            return cast(Optional[str], app.run())
        except Exception:
            self._print_keymap_fallback()
            return None

    def _print_keymap_fallback(self) -> None:
        """prompt_toolkit 组件不可用时的降级: 直接打印键位表。"""
        for grp_name, items in self._key_groups():
            self.console.print(Text(f"  {grp_name}", style=C["accent"]))
            for key, desc in items:
                self.console.print(Text(f"    {key:<12} {desc}", style=C["dim"]))
