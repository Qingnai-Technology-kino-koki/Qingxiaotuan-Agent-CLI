"""青小团 TUI —— 单一 Application (启动加载屏 + 对话界面)。

以 splash.py 的蓝色吉祥物加载屏为基底, 扩展出对话模块:
  - 启动期: 内嵌加载屏 (蓝字吉祥物 ▐█▛█▛█▌ + braille 转圈 + 进度条 + Ctrl-C 退出)
  - 就绪后: 对话界面 (对话区 + 输入栏 + 底部上下文条), 无侧栏, 保持简陋。

单一 Application / 事件循环: 启动加载屏与对话界面共用同一 App, 杜绝
Splash→TUI 双全屏 alt-screen 切换在 Windows Terminal(conpty) 下的崩溃与卡顿。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..i18n import t

_HAS_PT = False
try:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (ConditionalContainer, Dimension, HSplit,
                                        Layout, VSplit, Window)
    from prompt_toolkit.filters import to_filter
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import TextArea
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.output.base import DummyOutput
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.completion import Completer, Completion
    _HAS_PT = True
except Exception:
    pass


# ---- 配色: 青小团自研的深色视觉语言 ----
# 主界面观感与 Kimi Code 保持一致的清爽蓝色系; 法定署名见仓库根 NOTICE。
_PRIMARY = "#4FA8FF"      # 主品牌蓝: 盒子边框 / 吉祥物 / 标题 / 输入框提示符
_BLUE_DIM = "#5E97CC"     # 次级蓝: 盒子副标题 / 信息行

# 通用界面 (对话 / 输入 / 底栏) 的深色字号层次
_TEXT = "#E0E0E0"
_TEXT_STRONG = "#F5F5F5"
_DIM = "#888888"
_MUTED = "#6B6B6B"
_BORDER = "#5A5A5A"
_ACCENT = "#5BC0BE"
_SUCCESS = "#4EC87E"
_WARNING = "#E8A838"
_ERROR = "#E85454"
_ROLEUSER = "#FFCB6B"
_SHELL = "#BD93F9"

VERSION = "v0.2.014"      # 与 pyproject.toml 保持一致的真实版本

# 加载屏转圈帧
_SPLASH_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# 思考态月亮转圈帧 (每帧 90ms, 8 相走完一圈约 0.72s, 观感更流畅稳定)
_MOON_FRAMES = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
_MOON_INTERVAL = 0.09

# 吉祥物字符标
KIMI_LOGO = ['▐█▛█▛█▌', '▐█████▌']

# prompt_toolkit 缺失时 (仅 import 本模块) 也要能安全导入:
# STYLE 降级为 None, 真正使用 TUI 时自会因缺少 pt 而报清晰错误。
_STYLE_DICT = {
    # 盒子 (吉祥物 / 模型 / 目录) 保持主品牌蓝
    "title": _PRIMARY,        # 盒子: 吉祥物 / 标题 (蓝)
    "boxsub": _BLUE_DIM,      # 盒子: 副标题 / 信息行 (Directory/Model/Version, 蓝)
    "dim": _DIM,
    "text": _TEXT,
    "textstrong": _TEXT_STRONG,
    "textdim": _DIM,
    "textmuted": _MUTED,
    "accent": _ACCENT,
    "error": _ERROR,
    "warning": _WARNING,
    "success": _SUCCESS,
    "primary": _PRIMARY,      # 盒子边框 / 输入框提示符 (蓝)
    "roleuser": _ROLEUSER,    # 用户消息: 金
    "border": _BORDER,        # 输入框边框: 灰
    "shellmode": _SHELL,
}
STYLE = Style.from_dict(_STYLE_DICT) if _HAS_PT else None

# 对话区符号约定
USER_MESSAGE_BULLET = "✨ "
STATUS_BULLET = "● "
SUCCESS_MARK = "✓"
FAILURE_MARK = "✗"


def _disp_width(text: str) -> int:
    """显示宽度 (CJK/绘图形字符按实际列宽), 用于对齐欢迎盒右边框。"""
    try:
        from prompt_toolkit.utils import get_cwidth
        return sum(get_cwidth(ch) for ch in text)
    except Exception:
        return len(text)


def _split_flat_lines(paste: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    """把扁平片段列表 (含 "\\n" 分隔) 切成逐行片段列表。"""
    lines: List[List[Tuple[str, str]]] = []
    cur: List[Tuple[str, str]] = []
    for cls, seg in paste:
        if seg == "\n":
            lines.append(cur)
            cur = []
        else:
            cur.append((cls, seg))
    if cur:
        lines.append(cur)
    return lines if lines else [[]]


class _SlashCompleter(Completer):
    """TUI 输入框的命令补全: 同时支持 `/cmd` 与 Kimi 同款 `+/cmd`。

    只对以 `/` 或 `+/` 开头的输入弹出命令提示; 选择补全项时会把 `+` 前缀
    一并写回, 使 `+/help` 这类命令能直接被回车执行。
    """

    def __init__(self, words: List[str]) -> None:
        self._words = list(words)

    def get_completions(self, document, complete_event):  # noqa: C901
        text = document.text_before_cursor
        # 仅在命令前缀下补全: `/xxx` 或 `+/xxx`
        if text.startswith("+/"):
            plus, cut = "+", 2
            rest = text[2:]
        elif text.startswith("/"):
            plus, cut = "", 1
            rest = text[1:]
        else:
            return
        rest = rest.lower()
        meta = t("tui.cmd_meta")
        for w in self._words:
            name = w[1:]  # 去掉前导 "/"
            if rest and not name.startswith(rest):
                continue
            full = plus + w
            yield Completion(
                full,
                start_position=-cut - len(rest),  # 替换光标前的命令前缀+已输入部分
                display=w,
                display_meta=meta,
            )


class QxtTUI:
    """青小团 TUI: 蓝色吉祥物加载屏 + 对话界面 (无侧栏, 单一 Application)。

    对外接口与 cmd_chat.py 完全对齐:
      QxtTUI(on_submit, title=, workspace=, on_cancel=, on_command=)
      方法: set_boot_progress / set_ready / boot_error / begin_tool / end_tool /
            end_any_tool / stream_assistant / end_stream / append_log /
            set_context_info / set_model / set_tools / set_plan_mode /
            confirm / close / run ...
    """

    def __init__(
        self,
        on_submit: Callable[[str], Optional[str]],
        title: str = "青小团",
        workspace: str = ".",
        on_cancel: Optional[Callable[[], None]] = None,
        on_command: Optional[Callable[[str], Optional[str]]] = None,
        commands: Optional[List[str]] = None,
    ) -> None:
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.on_command = on_command
        self.title = title
        self.workspace = workspace
        # Kimi 同款 `+/命令` 补全词表: 传入则启用补全, 缺省 (兼容旧调用) 则不启用。
        self._commands: List[str] = list(commands or [])

        # 状态
        self._events: List[Any] = []
        self._closed = False
        self._busy = False
        self._cancel_requested = threading.Event()

        # 模式
        self._mode = "standard"
        self._plan_mode = False
        self._permission_mode = "default"
        self._no_key = False

        # Token / Context
        self._ctx_tokens: Optional[int] = None
        self._ctx_budget: Optional[int] = None
        self._context_pct: float = 0.0
        self._model_label = os.environ.get("QXT_MODEL_LABEL", "")
        self._tools: List[str] = []

        # 启动引导 (loading) 状态: 内核后台构建期间显示内嵌加载屏,
        # 就绪后切到对话界面。同一 Application / 事件循环, 杜绝双屏切换崩溃。
        self._booting = True
        self._boot_progress = 0
        self._boot_status = t("tui.boot_starting")
        self._boot_error: Optional[str] = None
        self._agent: Any = None
        self._frame = 0
        self._moon_frame = 0

        # 流式 / 工具状态
        self._stream_buf = ""
        self._streaming = False
        self._streamed = False
        self._running: List[str] = []
        self._tool_start_times: dict = {}
        # 抑制月亮: 流式收尾到回合真正结束之间, 不渲染"刚答完还转月亮"的鬼影。
        # 新一轮输入 / 新工具开始时清零, 保证多step回合里后续思考仍会显示。
        self._moon_suppressed = False

        # 审计面板 (kimi 折叠思考): 收集模型推理 + 重试/工具审计日志
        self._reason_buf: List[str] = []      # 本轮思考分片
        self._reason_full = ""                # 本轮累计思考全文
        self._audit_visible = False           # Ctrl+O 折叠面板开关
        self._audit_log: List[Tuple[str, str]] = []  # (cls, text) 审计行

        # 对话区滚动: 自定义跟随光标定位 (见 _log_cursor_pos), 支持回到历史
        # `follow` 为 True 时自动钉在最新内容底部; `pin` 为手动上翻时钉住的行号。
        self._follow = True
        self._pin = 0
        self._cursor_line = 0
        # 工具调用折叠 (Claude/TraeWork 风格 "see"): 本轮工具完成记录先折叠成
        # 一行摘要, Ctrl+O 展开查看每步细节 (同时镜像进审计面板)。
        self._turn_tool_lines: List[str] = []
        self._tool_detail = False

        # prompt_toolkit
        self._app: Optional[Any] = None
        self._log_control = FormattedTextControl(
            self._render_conversation,
            show_cursor=False,
            get_cursor_position=self._log_cursor_pos,
        )
        self._footer_control = FormattedTextControl(self._render_footer)
        self._input = TextArea(
            prompt=FormattedText([("class:primary", "> ")]),
            multiline=False,
            # 单行输入必须横向滚动: 若 wrap_lines=True 且 height=1, 中文/宽字符贴到
            # 右边界触发裁剪式软换行 → 最后列宽核算错一位, 高亮重绘(思考动画)把错位
            # 残影卷进输入栏, 表现为 IME 打字/粘贴/英文全变乱码。
            wrap_lines=False,
            scrollbar=False,
            height=1,
            history=InMemoryHistory(),
            # Kimi 同款命令补全: 输入 `/` 或 `+/` 时弹命令菜单 (仅当传入 commands)。
            completer=_SlashCompleter(self._commands) if self._commands else None,
            complete_while_typing=bool(self._commands),
        )

        # 输入框: 单行, 细边框, 极简
        _b = "class:border"
        # 顶边: ╭────╮ (圆角)
        input_top = VSplit([
            Window(width=1, char="╭", style=_b, height=1),
            Window(char="─", style=_b, height=1),
            Window(width=1, char="╮", style=_b, height=1),
        ])
        # 中间: │ 输入区 │
        input_mid = VSplit([
            Window(width=1, char="│", style=_b),
            self._input,
            Window(width=1, char="│", style=_b),
        ])
        # 底边: ╰────╯ (圆角)
        input_bottom = VSplit([
            Window(width=1, char="╰", style=_b, height=1),
            Window(char="─", style=_b, height=1),
            Window(width=1, char="╯", style=_b, height=1),
        ])
        input_frame = HSplit([
            input_top,
            input_mid,
            input_bottom,
        ])

        # 对话区占满剩余空间: 用普通 Window + 自定义光标定位实现滚动,
        # 替代 ScrollablePane (后者在 Windows/conpty 下碎滚动且无法回看历史)。
        self._log_window = Window(
            self._log_control,
            wrap_lines=True,
            height=Dimension(min=1, weight=1),
        )
        conversation = self._log_window
        # 审计面板 (kimi 折叠思考): 默认隐藏, Ctrl+O 展开; 展开约占 45% 高度
        self._audit_control = FormattedTextControl(self._render_audit)
        audit_wrap = ConditionalContainer(
            HSplit([
                Window(char="─" * 1, height=1, style="class:border"),
                Window(self._audit_control, wrap_lines=True, height=Dimension(min=3, weight=2)),
            ]),
            filter=to_filter(self._audit_visible),
        )
        layout = Layout(
            HSplit([
                conversation,
                # 审计面板: 仅当展开时占空间
                audit_wrap,
                # 输入框: 细边框, 单行高度
                input_frame,
                # 底部状态栏: 一行塞满所有信息
                Window(self._footer_control, height=1),
            ])
        )

        output = None
        try:
            is_test = bool(
                os.environ.get("PYTEST_CURRENT_TEST")
                or os.environ.get("PYTEST_VERSION")
                or os.environ.get("CI")
                or "pytest" in sys.modules
            )
            if is_test or not getattr(sys.stdout, "isatty", lambda: False)():
                output = DummyOutput()
            elif os.name == "nt":
                # Windows 原生体验 (Major #6): 真终端下先启用 VT 处理 + UTF-8 代码页,
                # 否则 prompt_toolkit 的 ANSI 颜色与 CJK 框线被 ConHost 渲染成乱码。
                from .win_compat import enable_virtual_terminal
                enable_virtual_terminal()
        except Exception:
            pass

        try:
            self._app = Application(
                layout=layout,
                key_bindings=self._build_keys(),
                style=STYLE,
                full_screen=True,
                mouse_support=False,
                output=output,
            )
        except Exception as exc:
            if type(exc).__name__ != "NoConsoleScreenBufferError":
                raise
            self._app = Application(
                layout=layout,
                key_bindings=self._build_keys(),
                style=STYLE,
                full_screen=True,
                mouse_support=False,
                output=DummyOutput(),
            )

    # ============================================================ 键位

    def _build_keys(self) -> "KeyBindings":
        kb = KeyBindings()

        @kb.add("c-c")
        def _(event):
            self._closed = True
            event.app.exit()

        @kb.add("c-q")
        def _(event):
            self._closed = True
            event.app.exit()

        @kb.add("enter")
        def _(event):
            self._handle_enter(event)

        @kb.add("c-o")
        def _(event):
            self.toggle_audit()

        # ---- 对话区回卷: PgUp/PgDn 上翻/下翻, Home/End 到顶/底 ----
        # (不用 Ctrl+U/Ctrl+D: 会被输入框的编辑键抢占, 见 TextArea buffer 绑定)
        @kb.add("pageup")
        def _(event):
            self._scroll_log(-self._page_rows())
            event.app.invalidate()

        @kb.add("pagedown")
        def _(event):
            self._scroll_log(self._page_rows())
            event.app.invalidate()

        @kb.add("home")
        def _(event):
            self._follow = False
            self._pin = 0
            event.app.invalidate()

        @kb.add("end")
        def _(event):
            self._follow = True
            self._pin = 0
            event.app.invalidate()

        # ---- Kimi 同款命令补全: Tab 开始/循环, 方向键在命令菜单中游走 ----
        @kb.add("c-i")  # Tab
        def _(event):
            b = event.current_buffer
            if b.complete_state:
                b.complete_next()
            else:
                b.start_completion(select_first=True)

        @kb.add("down")
        def _(event):
            b = event.current_buffer
            if b.complete_state:
                b.complete_next()

        @kb.add("up")
        def _(event):
            b = event.current_buffer
            if b.complete_state:
                b.complete_previous()

        return kb

    def toggle_audit(self) -> None:
        """Ctrl+O 切换"see"折叠: 收起/展开工具调用详情 + 审计面板 (思考过程)。

        折叠态在对话区只显示一行 "see N tool calls", 展开态逐条展示每步调用;
        审计面板同步显示完整思考过程与工具记录。类似 Claude/TraeWork 的折叠观察窗。
        """
        self._audit_visible = not self._audit_visible
        self._tool_detail = self._audit_visible
        if self._app and self._app.is_running:
            self._app.invalidate()

    # ---- 对话区滚动 (自定义光标定位, Windows/conpty 下可靠) ----

    def _page_rows(self) -> int:
        """单次翻页的行数 = 可视区高度 (减输入框 3 行 + 底栏 1 行)。"""
        try:
            rows = self._app.output.get_size().rows if self._app else 0
        except Exception:
            rows = 0
        if rows <= 0:
            try:
                rows = shutil.get_terminal_size((80, 24)).lines
            except Exception:
                rows = 24
        return max(3, rows - 4)

    def _scroll_log(self, delta: int) -> None:
        """按 delta 行上/下滚动对话区。到顶/到底时分别转 follow=False/True。"""
        n = self._current_line_count()
        if n <= 0:
            return
        if self._follow:
            # 首次上翻: 从当前底部位置起算
            self._follow = False
            self._pin = max(0, n - self._page_rows())
        self._pin = max(0, min(n - 1, self._pin + delta))
        if self._pin >= n - 1:
            self._follow = True
            self._pin = 0

    def _log_cursor_pos(self):
        """返回对话区应钉住的光标行号 (FormattedTextControl.get_cursor_position)。

        follow 时钉在最后一行 -> Window 自动滚动到底部 (跟随新内容);
        手动上翻时钉在 _pin -> 视图稳定在被选历史处, 不会因新内容跳动。
        """
        return Point(x=0, y=self._cursor_line)

    def _current_line_count(self) -> int:
        """当前渲染内容的段落行数 (供滚动钳位)。"""
        try:
            lines = self._scrolled_lines(logic_only=True)
            return len(lines)
        except Exception:
            return 0

    def _render_audit(self):
        """审计面板内容: 顶部标题 + 思考全文 + 审计日志 (kimi 折叠思考)。"""
        line: List[Tuple[str, str]] = [("class:primary", t("tui.audit_title"))]
        if not self._audit_visible:
            return line
        line.append(("class:textdim", "  Ctrl+O 收起"))
        out: List[List[Tuple[str, str]]] = [line]
        # 进行中的思考 (实时)
        if self._reason_full:
            out.append([("class:textdim", self._reason_full)])
        for cls, text in self._audit_log[-40:]:
            out.append([(cls, text)])
        if len(out) == 1:
            out.append([("class:textdim", t("tui.audit_empty"))])
        flat: List[Tuple[str, str]] = []
        for i, r in enumerate(out):
            for cls, seg in r:
                flat.append((cls, seg))
            if i < len(out) - 1:
                flat.append(("", "\n"))
        return flat

    def _push_history(self, text: str) -> None:
        """把输入写入输入框历史, 跳过与上一条完全相同的重复输入。

        配合 ↑/↓ 历史导航: 连发同一条命令/多轮同句式时不刷屏, 回看更干净。
        历史读写失败绝不影响发送主流程。
        """
        try:
            history = self._input.buffer.history
            if history is None:
                return
            try:
                entries = history.get_strings()
                if entries and entries[-1] == text:
                    return
            except Exception:
                pass
            history.append_string(text)
        except Exception:
            pass

    def _handle_enter(self, event) -> None:
        text = self._input.text.strip()
        if not text:
            return
        if self._booting or self._boot_error:
            self.append_log(t("tui.booting_wait"))
            return
        if self._busy:
            self.append_log(t("tui.busy_wait"))
            return

        self._push_history(text)
        self._input.buffer.reset()
        self._busy = True
        self._cancel_requested.clear()
        self._moon_suppressed = False  # 新一轮回合, 思考月亮恢复可用
        # 新一轮: 重置工具折叠缓冲, 并回到自动跟随底部 (让最新回复可见)
        self._turn_tool_lines = []
        self._tool_detail = False
        self._follow = True
        self._pin = 0

        # 命令: 支持 `/cmd` 与 Kimi 同款 `+/cmd` (归一化后统一走 on_command)
        cmd = None
        if text.startswith("/"):
            cmd = text
        elif text.startswith("+/"):
            cmd = "/" + text[2:]
        if cmd and self.on_command:
            result = self.on_command(cmd)
            if result:
                self.append_log(result)
            self._busy = False
            event.app.invalidate()
            return

        self.append_log(f"{USER_MESSAGE_BULLET}{text}")
        threading.Thread(
            target=self._run_submit, args=(text,),
            name="qxt-submit", daemon=True,
        ).start()

    # ============================================================ 启动引导 (加载屏)

    def set_boot_progress(self, pct: int, status: str = "") -> None:
        self._boot_progress = min(100, max(0, int(pct)))
        if status:
            self._boot_status = status
        if self._app and self._app.is_running:
            self._app.invalidate()

    def set_ready(self, config: Any, agent: Any, workspace: str,
                  tools: Optional[List[str]] = None, no_key: bool = False,
                  mode: Optional[str] = None) -> None:
        """内核就绪: 从加载屏切换到对话界面。mode=standard/yolo/plan 驱动底部徽标。"""
        self._booting = False
        self._boot_error = None
        self._no_key = bool(no_key)
        self._agent = agent
        if tools:
            self.set_tools(list(tools))
        try:
            agent.ctx.confirm = self.confirm
        except Exception:
            pass
        if mode:
            # 显式传入的运行模式优先 (standard / yolo / plan)
            self.set_mode(mode)
        else:
            self.set_plan_mode(getattr(agent, "plan_mode", False))
        prov = config.get("model.provider", "") or ""
        mdl = config.get("model.model", "") or ""
        self._model_label = f"{prov}/{mdl}"
        # 初始化真实上下文占用条: 预算取模型真实上下文窗口 (fallback 到配置)
        self._ctx_budget = self._model_context_window(prov, mdl)
        self.set_context_info(0, self._ctx_budget)
        self._show_welcome()
        hint = t("tui.readonly_hint")
        if no_key:
            hint += " " + t("tui.no_key_hint")
        # 只读提示作为普通日志行展示 (紧凑布局, 不单独占 hint 行)
        self.append_log(hint, cls="class:textdim")
        if self._app and self._app.is_running:
            self._app.invalidate()

    def boot_error(self, message: str) -> None:
        """内核构建失败: 显示错误态 (不再静默闪退), 用户可读后 Ctrl-Q 退出。"""
        self._boot_error = str(message)
        self._booting = False
        self.append_log(f"{t('tui.boot_error_title')} {message}")
        self.append_log(t("tui.boot_error_hint"))
        if self._app and self._app.is_running:
            self._app.invalidate()

    def _box_width(self) -> int:
        """盒子/输入框可用宽度 = 真实 App 列宽 (避免与 wrap_lines 的窗口宽度偏差导致错位/圆角外拐)。"""
        try:
            if self._app is not None:
                w = self._app.output.get_size().columns
                if w and w > 0:
                    return max(44, w)
        except Exception:
            pass
        try:
            return max(44, shutil.get_terminal_size((80, 24)).columns)
        except Exception:
            return 80

    def _kimi_box(self, title: str, subtitle: str,
                   info_pairs: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
        """欢迎盒: 全宽圆角边框 + 2 空格内边距 + 吉祥物与标题并排 + 标签(蓝次级)/值(浅色)。

        返回 List[行]; 每行是 (class, text) 分段列表, 便于在 events 路径逐行渲染,
        保证 CJK/绘图形字符下右边框仍对齐。
        """
        width = self._box_width()           # 真实 App 列宽, 全宽
        inner = max(1, width - 2)            # 边框内侧显示列数 (= 宽度 - 左右两个 │), 右侧竖线对齐
        pad = "  "
        pad_w = 2
        logo_w = max(_disp_width(r) for r in KIMI_LOGO)
        gap_w = 2
        head_text_w = max(4, inner - pad_w - logo_w - gap_w)
        info_w = max(4, inner - pad_w)

        def clip_dw(s: str, n: int) -> str:
            if _disp_width(s) > n:
                res = ""
                for ch in s:
                    if _disp_width(res) + _disp_width(ch) > n:
                        break
                    res += ch
                return res
            return s + " " * (n - _disp_width(s))

        # 头部两行: 吉祥物 + 右侧文字并排 (row0 蓝标题 / row1 蓝次级)
        head: List[List[Tuple[str, str]]] = []
        rights = [clip_dw(title, head_text_w), clip_dw(subtitle, head_text_w)]
        for i, lg in enumerate(KIMI_LOGO):
            seg: List[Tuple[str, str]] = []
            seg.append(("class:primary", "│"))
            seg.append(("", pad))
            seg.append(("class:title", lg.ljust(max(1, logo_w))))
            seg.append(("", "  "))
            r = rights[i]
            seg.append(("class:title" if i == 0 else "class:boxsub", r))
            rp = max(0, (inner - pad_w - logo_w - gap_w) - _disp_width(r))
            seg.append(("", " " * rp))
            seg.append(("class:primary", "│"))
            head.append(seg)

        # 信息行: 标签(蓝次级) + 值(浅色)
        info_rows: List[List[Tuple[str, str]]] = []
        for label, value in info_pairs:
            seg = []
            seg.append(("class:primary", "│"))
            seg.append(("", pad))
            seg.append(("class:boxsub", label))
            seg.append(("class:text", value))
            rp = max(0, info_w - _disp_width(label) - _disp_width(value))
            seg.append(("", " " * rp))
            seg.append(("class:primary", "│"))
            info_rows.append(seg)

        blank = [("class:primary", "│" + " " * (width - 2) + "│")]
        rows: List[List[Tuple[str, str]]] = [
            [("class:primary", "╭" + "─" * (width - 2) + "╮")],
            *head,
            blank,
            *info_rows,
            [("class:primary", "╰" + "─" * (width - 2) + "╯")],
        ]
        return rows

    def _render_boot(self) -> List[Tuple[str, str]]:
        frame = _SPLASH_FRAMES[self._frame % len(_SPLASH_FRAMES)]
        out: List[Tuple[str, str]] = []
        box_rows = self._kimi_box(
            title=t("tui.welcome_title"),
            subtitle=t("tui.welcome_subtitle"),
            info_pairs=[],
        )
        for i, row in enumerate(box_rows):
            for cls, seg in row:
                out.append((cls, seg))
            if i < len(box_rows) - 1:
                out.append(("", "\n"))
        out.append(("", "\n"))
        if self._boot_error:
            out.append(("class:error", f"  ✗ {self._boot_error}\n"))
            out.append(("class:textdim", f"  {t('tui.exit_ctrl_c')}\n"))
        elif self._boot_progress >= 100:
            out.append(("class:success", f"  {SUCCESS_MARK} {t('fs.ready')}\n"))
        else:
            out.append(("class:text", f"  {frame} {self._boot_status}\n"))
        bar_len = 30
        filled = int(self._boot_progress / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        out.append(("class:dim", f"  [{bar}] {self._boot_progress}%\n"))
        out.append(("", "\n"))
        out.append(("class:dim", f"  {t('tui.exit_ctrl_c')}\n"))
        return out

    # ============================================================ 流式 / 工具

    def stream_assistant(self, delta: str) -> None:
        if not delta:
            return
        self._streaming = True
        self._streamed = True
        self._stream_buf += delta
        if self._app and self._app.is_running:
            self._app.invalidate()

    def stream_reason(self, delta: str) -> None:
        """接收模型推理分片, 累积成思考全文, 写入审计面板。"""
        if not delta:
            return
        self._reason_buf.append(delta)
        self._reason_full += delta
        if self._app and self._app.is_running:
            self._app.invalidate()

    def audit(self, cls: str, text: str) -> None:
        """向审计面板追加一行日志 (类标志: textdim/accent/warning 等)。"""
        self._audit_log.append((cls, str(text)))
        if len(self._audit_log) > 500:
            self._audit_log = self._audit_log[-500:]
        if self._app and self._app.is_running:
            self._app.invalidate()

    def end_stream(self) -> None:
        if self._stream_buf:
            self._events.append(f"{STATUS_BULLET}{self._stream_buf}")
            self._stream_buf = ""
        # 本轮思考收尾进审计面板: 折叠后首行是标题, 展开看全文
        if self._reason_full:
            self._audit_log.append(("class:primary", "▸ 思考 (本轮)"))
            self._audit_log.append(("class:textdim", self._reason_full))
            self._reason_buf = []
            self._reason_full = ""
        self._streaming = False
        # 流式收尾: 到回合结束前抑制月亮, 避免与刚落库的回复同屏出现鬼影。
        # 若回合不结束, 由随后 begin_tool 重启指示。
        self._moon_suppressed = True
        # 收尾刷新交给 _run_submit 的 finally (彼时 _busy 已清)。
        if not self._busy and self._app and self._app.is_running:
            self._app.invalidate()

    def begin_tool(self, name: str) -> None:
        self._running.append(name)
        self._busy = True
        self._moon_suppressed = False  # 新工具开始: 后续思考重新亮出指示
        self._tool_start_times[name] = time.monotonic()
        if self._app and self._app.is_running:
            self._app.invalidate()

    def end_tool(self, name: str, ok: bool = True) -> None:
        if name in self._running:
            self._running.remove(name)
        mark = SUCCESS_MARK if ok else FAILURE_MARK
        elapsed = self._tool_start_times.pop(name, None)
        elapsed_str = f" ({elapsed:.1f}s)" if elapsed is not None else ""
        label = t("tui.tool_done") if ok else t("tui.tool_failed")
        line = f"{mark}{name} {label}{elapsed_str}"
        # 工具完成记录进折叠缓冲 (see N tool calls), 并镜像进审计面板供 Ctrl+O 展开。
        self._turn_tool_lines.append(line)
        self._audit_log.append(("class:success" if ok else "class:error", line))
        if len(self._audit_log) > 500:
            self._audit_log = self._audit_log[-500:]
        # _busy 由回合生命周期 (submit 开始置 True、finally 清) 统一掌管, 不在
        # 工具中途置 False, 否则多步回合会反复"掉指示/复现"造成月亮闪烁。
        if self._app and self._app.is_running:
            self._app.invalidate()

    def end_any_tool(self) -> None:
        if self._running:
            self.end_tool(self._running[0], ok=False)

    def set_model(self, label: str) -> None:
        self._model_label = str(label or "")
        if self._app and self._app.is_running:
            self._app.invalidate()

    def set_tools(self, tools: List[str]) -> None:
        self._tools = list(tools or [])

    def set_mode(self, mode: str) -> None:
        """设置运行模式 (standard / yolo / plan), 底部状态栏徽标随之更新。"""
        self._mode = mode or "standard"
        self._plan_mode = (mode == "plan")
        if self._app and self._app.is_running:
            self._app.invalidate()

    def set_plan_mode(self, enabled: bool) -> None:
        self._plan_mode = bool(enabled)
        if enabled:
            self._mode = "plan"
        elif self._mode == "plan":
            self._mode = "standard"

    def set_context_info(self, estimated: int, budget: int) -> None:
        self._ctx_tokens = int(estimated)
        self._ctx_budget = int(budget) if budget else 0
        bud = self._ctx_budget
        # 关键: budget 为 0/未知时回落到 0%, 不得算出 300000% 之类荒谬值
        self._context_pct = (
            min(100.0, self._ctx_tokens / bud * 100.0) if bud > 0 else 0.0
        )
        if self._app and self._app.is_running:
            self._app.invalidate()

    def set_context_used(self, estimated: int) -> None:
        """刷新上下文已用 token, 保持分母 (模型上下文窗口) 不变。

        只在回合结束后更新已用量, 不改 budget, 从而避免分母在"模型窗口/配置预算"
        之间来回切换造成的百分比跳变 (此前肉眼观感随机)。
        """
        self._ctx_tokens = max(0, int(estimated))
        bud = self._ctx_budget
        self._context_pct = (
            min(100.0, self._ctx_tokens / bud * 100.0) if bud > 0 else 0.0
        )
        if self._app and self._app.is_running:
            self._app.invalidate()

    _PROVIDER_CONTEXT: Dict[str, int] = {
        "deepseek": 64 * 1024,
        "moonshot": 128 * 1024,
        "openai": 128 * 1024,
        "anthropic": 200_000,
        "openrouter": 200_000,
        "siliconflow": 128 * 1024,
        "qwen": 128 * 1024,
        "dashscope": 128 * 1024,
        "zhipu": 128 * 1024,
        "doubao": 256 * 1024,
        "volcengine": 256 * 1024,
        "groq": 128 * 1024,
        "github": 128 * 1024,
        "ollama": 32 * 1024,
    }

    def _model_context_window(self, provider: str, model: str) -> int:
        """推断当前模型真实上下文窗口 (token), 驱动底栏 context 分母。

        优先看模型名里的显式尺寸后缀 (如 doubao-1.5-pro-256k → 256k), 否则落到
        供应商级默认窗口, 再兜底 128k。纯展示用途, 别拿它当硬性预算。
        """
        m = (model or "").lower()
        import re
        # 模型名显式标注尺寸, 例如: -256k / -128k / -1m / flash-8b-4096
        m_suffix = re.search(r"[-_](\d{2,4})k$", m)
        if m_suffix:
            return int(m_suffix.group(1)) * 1024
        if re.search(r"[-_]1m$", m) or re.search(r"[-_]256k", m):
            return 256 * 1024
        if re.search(r"[-_]200k", m):
            return 200_000
        if re.search(r"[-_]max|long|pro", m):
            return 128 * 1024
        prov = (provider or "").lower()
        # 供应商别名归一到同一窗口
        if prov in ("aliyun", "dashscope"):
            prov = "qwen"
        for key, win in self._PROVIDER_CONTEXT.items():
            if key in prov:
                return win
        return 128 * 1024

    def _context_string(self) -> str:
        """底部上下文占用: context: X% (est/budget)。"""
        est = self._ctx_tokens or 0
        bud = self._ctx_budget or 0
        pct = min(100.0, est / bud * 100.0) if bud > 0 else 0.0
        self._context_pct = pct

        def _fmt(n: int) -> str:
            if n >= 1_000_000:
                return f"{n / 1_000_000:.0f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}k"
            return str(n)

        return f"context: {pct:.0f}% ({_fmt(est)}/{_fmt(bud)})"

    def append_log(self, text: str, cls: Optional[str] = None) -> None:
        if cls:
            self._events.append((cls, str(text)))
        else:
            self._events.append(str(text))
        if len(self._events) > 500:
            del self._events[:-500]
        if self._app and self._app.is_running:
            self._app.invalidate()

    def confirm(self, prompt: str, *, expected: Optional[str] = None,
                position: Optional[int] = None) -> bool:
        """安全多阶段确认 (TUI 内用 run_in_terminal 挂起应用读取确认码)。"""
        from qingxiaotuan.core.whitelist import interactive_confirm
        result = [False]

        def _read() -> None:
            result[0] = interactive_confirm(prompt, expected=expected, position=position)

        app = self._app
        if app is not None and hasattr(app, "run_in_terminal"):
            try:
                app.run_in_terminal(_read)
                return result[0]
            except Exception:
                return interactive_confirm(prompt, expected=expected, position=position)
        return interactive_confirm(prompt, expected=expected, position=position)

    # ============================================================ 运行

    def _run_submit(self, text: str) -> None:
        try:
            result = self.on_submit(text)
            self.end_stream()
            if result and not self._streamed:
                self.append_log(result)
        except Exception as exc:
            self.append_log(f"[错误] {type(exc).__name__}: {exc}")
        finally:
            self._busy = False
            self._streamed = False
            self._streaming = False
            self._moon_suppressed = False
            self._running.clear()
        if self._app and self._app.is_running:
            self._app.invalidate()

    def run(self) -> None:
        """启动 TUI: 默认处于加载态, 内核就绪后由 set_ready 切换。

        单一 Application / 事件循环: 不再有独立 Splash 全屏 App,
        因此不存在双屏 alt-screen 切换在 Windows Terminal(conpty) 下的崩溃。

        若全屏模式在特殊终端下仍崩溃, 自动以内联模式 (full_screen=False,
        不抢占整屏/不走 alt-screen) 重试一次; 仍失败则抛给调用方回退 REPL。
        """
        animator = threading.Thread(target=self._animate, name="qxt-anim", daemon=True)
        animator.start()
        try:
            if self._app is not None:
                self._app.run()
        except BaseException:
            import traceback as _rt
            _rt.print_exc()
            # 全屏 alt-screen 在某些 conpty 配置下会崩溃 -> 退回内联模式重试
            if self._app is not None:
                try:
                    from prompt_toolkit import Application
                    self._app = Application(
                        layout=self._app.layout,
                        key_bindings=self._app.key_bindings,
                        style=STYLE,
                        full_screen=False,
                        mouse_support=False,
                    )
                    self._app.run()
                    return
                except BaseException:
                    _rt.print_exc()
            raise
        finally:
            self._closed = True

    def close(self) -> None:
        self._closed = True
        if self._app and self._app.is_running:
            self._app.exit()

    def _animate(self) -> None:
        while not self._closed:
            if self._booting:
                self._frame = (self._frame + 1) % len(_SPLASH_FRAMES)
                time.sleep(0.08)
            elif self._busy or self._streaming or self._running:
                # 模型思考/流式/工具期间: 月亮转圈, 每帧 120ms
                self._moon_frame = (self._moon_frame + 1) % len(_MOON_FRAMES)
                self._frame = (self._frame + 1) % len(_SPLASH_FRAMES)
                time.sleep(_MOON_INTERVAL)
            else:
                time.sleep(0.3)
            if self._app and self._app.is_running:
                self._app.invalidate()

    # ============================================================ 渲染: 对话区

    def _msg_to_lines(self, role: str, text: str) -> List[List[Tuple[str, str]]]:
        if role == "user":
            bullet = USER_MESSAGE_BULLET
            bcls = ccls = "class:roleuser"
        else:
            bullet = STATUS_BULLET
            bcls = ccls = "class:text"
        lines = str(text).split("\n")
        out: List[List[Tuple[str, str]]] = []
        for i, ln in enumerate(lines):
            if i == 0:
                seg: List[Tuple[str, str]] = []
                if bullet:
                    seg.append((bcls, bullet))
                if ln:
                    seg.append((ccls, ln))
                out.append(seg)
            else:
                out.append([(ccls, "  " + ln)])
        return out

    def _colored_lines(self, cls: str, text: str) -> List[List[Tuple[str, str]]]:
        out: List[List[Tuple[str, str]]] = []
        for ln in str(text).split("\n"):
            out.append([(cls, ln)] if ln else [("", ln)])
        return out

    def _scrolled_lines(self, logic_only: bool = False) -> List[List[Tuple[str, str]]]:
        """构建对话区完整内容 (段落行列表), 并按滚动状态钉住光标行号。

        - 工具完成记录默认折叠为一行 "see N tool calls", Ctrl+O 展开逐条;
        - 返回的 list 每项是一行 (list[ (cls,text) ])。
        """
        if self._booting or self._boot_error:
            paste = self._render_boot()
            if logic_only:
                return [[seg for seg in line] for line in _split_flat_lines(paste)]
            self._cursor_line = 0
            return _split_flat_lines(paste)

        screen: List[List[Tuple[str, str]]] = []
        for ev in self._events:
            if isinstance(ev, tuple):
                screen.extend(self._colored_lines(ev[0], ev[1]))
            elif isinstance(ev, list):
                screen.append(ev)
            else:
                role = "user" if ev.startswith(USER_MESSAGE_BULLET) else "log"
                screen.extend(self._msg_to_lines(role, ev))

        # 本轮工具调用: 折叠成一行摘要 (see N), 展开时逐条; 始终镜像进审计面板
        if self._turn_tool_lines:
            if self._tool_detail:
                for ln in self._turn_tool_lines:
                    screen.append([("class:textdim", ln)])
            else:
                n = len(self._turn_tool_lines)
                screen.append([("class:primary", f"▸ see {n} tool calls (Ctrl+O)")])

        # 流式
        if self._streaming and self._stream_buf:
            screen.append([("class:text", STATUS_BULLET + self._stream_buf)])
        elif self._busy and not self._running and not self._streaming and not self._moon_suppressed:
            moon = _MOON_FRAMES[self._moon_frame % len(_MOON_FRAMES)]
            screen.append([("class:primary", f"{moon} {t('tui.thinking')}")])
        # 在途工具
        for name in self._running:
            elapsed = self._tool_start_times.get(name)
            elapsed_str = f" ({time.monotonic() - elapsed:.1f}s)" if elapsed else ""
            screen.append([("class:textdim", t("tui.tool_run", name=name, time=elapsed_str))])

        if not screen:
            screen.append([("class:textdim", t("tui.ready_empty"))])

        if not logic_only:
            # 滚动定位: follow -> 钉在最后一行 (Window 自动滚到底); 否则钉在 _pin
            n = len(screen)
            if self._follow:
                self._pin = 0
                self._cursor_line = n - 1
            else:
                self._pin = max(0, min(n - 1, self._pin))
                self._cursor_line = self._pin
        return screen

    def _render_conversation(self):
        """扁平化对话内容供 FormattedTextControl 渲染 (行间加 \\n)。"""
        screen = self._scrolled_lines()
        if not screen:
            return [("class:textdim", t("tui.ready_empty"))]
        flat: List[Tuple[str, str]] = []
        for i, line in enumerate(screen):
            for cls, seg in line:
                flat.append((cls, seg))
            if i < len(screen) - 1:
                flat.append(("", "\n"))
        return flat

    def _render_header(self):
        """顶部状态 HUD: 实时状态(启动转圈/思考月亮/就绪勾) + 模式 + 模型。"""
        parts: List[Tuple[str, str]] = []
        if self._booting:
            frame = _SPLASH_FRAMES[self._frame % len(_SPLASH_FRAMES)]
            parts.append(("class:primary", f"{frame} {t('tui.boot_starting')}"))
        elif self._busy or self._running or self._streaming:
            moon = _MOON_FRAMES[self._moon_frame % len(_MOON_FRAMES)]
            parts.append(("class:primary", f"{moon} {t('fs.processing')}"))
        else:
            parts.append(("class:success", f"{SUCCESS_MARK} {t('fs.ready')}"))
        if self._plan_mode:
            parts.append(("class:primary", f"  [{t('tui.plan_badge')}]"))
        if self._model_label:
            parts.append(("class:text", f"  {self._model_label}"))
        return parts

    def _render_footer(self):
        """单行底部状态栏: 模式 + 模型 + 状态 + 目录 + git + context。

        格式: standard/yolo/plan  model thinking  /path/to/wd  main [±]    context: 5% (3.0k/60.0k)
        """
        parts: List[Tuple[str, str]] = []

        # 模式徽标 (standard / yolo / plan)
        mode_label = self._mode or "standard"
        if mode_label == "yolo":
            mode_cls = "class:error"
        elif mode_label == "plan":
            mode_cls = "class:primary"
        else:
            mode_cls = "class:warning"
        parts.append((mode_cls, mode_label))

        # Kimi 同款命令行提示: 仅当启用了命令补全且空闲时展示, 保持简短
        if self._commands and not self._busy and not self._streaming:
            parts.append(("class:textdim", f"  {t('tui.cmd_hint')}"))

        # 模型名 + 状态 (未配 key / thinking / ready / running tool)
        model = self._model_label or "default"
        if self._no_key and not self._booting:
            status = t("tui.no_key_hint")
            status_cls = "class:warning"
        elif self._booting:
            status = t("tui.boot_starting")
            status_cls = "class:textdim"
        elif self._busy or self._streaming:
            status = t("tui.thinking")
            status_cls = "class:text"
        elif self._running:
            tool = self._running[-1] if self._running else ""
            status = tool if tool else t("fs.ready")
            status_cls = "class:accent"
        else:
            status = t("fs.ready")
            status_cls = "class:success"
        parts.append(("class:text", f"  {model} "))
        parts.append((status_cls, status))

        # 工作目录
        cwd = os.path.basename(os.path.normpath(self.workspace)) or self.workspace
        parts.append(("class:textdim", f"  {cwd}"))

        # git 分支 (探测)
        git_branch = self._git_branch()
        if git_branch:
            parts.append(("class:textdim", f"  {git_branch}"))

        # 右侧: context 占用
        ctx_str = self._context_string()
        # 用空格推到右边: 估算剩余宽度, 用空格填充
        # 简化: 直接放后面, 前面加足够空格让它靠右
        parts.append(("class:textdim", "    "))
        parts.append(("class:textdim", ctx_str))

        return parts

    def _git_branch(self) -> str:
        """探测当前 git 分支 (带缓存, 避免每行渲染都调 git)。"""
        if not hasattr(self, "_git_branch_cache"):
            self._git_branch_cache = None
            self._git_branch_ts = 0.0
        now = time.monotonic()
        if now - self._git_branch_ts < 5.0 and self._git_branch_cache is not None:
            return self._git_branch_cache or ""
        try:
            import subprocess
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.workspace, capture_output=True, text=True, timeout=1
            )
            if r.returncode == 0 and r.stdout.strip():
                branch = r.stdout.strip()
                # 探测是否有未提交改动
                r2 = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=self.workspace, capture_output=True, text=True, timeout=1
                )
                dirty = " [±]" if r2.stdout.strip() else ""
                self._git_branch_cache = branch + dirty
            else:
                self._git_branch_cache = ""
        except Exception:
            self._git_branch_cache = ""
        self._git_branch_ts = now
        return self._git_branch_cache or ""

    def _show_welcome(self) -> None:
        """就绪后展示欢迎盒 (吉祥物/模型/项目目录)。

        盒子逐行作为事件 (分段列表) 追加, 由 _render_conversation 按行渲染,
        保证 CJK/绘图形字符下右边框仍对齐。
        """
        cwd = os.path.abspath(self.workspace)
        session = getattr(self, "_session_id", "") or "new-session"
        model = self._model_label or "default"
        info_pairs = [
            (f"{t('banner.directory')}: ", cwd),
            (f"{t('banner.session')}:   ", session),
            (f"{t('banner.model')}:     ", model),
            (f"{t('banner.version')}:   ", VERSION),
        ]
        for row in self._kimi_box(
            title=t("tui.welcome_title"),
            subtitle=t("tui.welcome_subtitle"),
            info_pairs=info_pairs,
        ):
            self._events.append(row)
