"""UI —— 多行 REPL + 实时思考/工具状态展示。

视觉风格采用 Kimi Code CLI 的终端 TUI 范式 (青小团专属配色):
- 启动 banner: 简洁双线信息盒, 无 ASCII 吉祥物, 蓝 (primary) 主调 + teal (accent) 强调。
- 主色 primary=#4FA8FF (蓝), 强调 accent=#5BC0BE (青绿); 成功绿 / 警告琥珀 / 错误红。
- 工具调用以 ▶ 前缀 + 树形 └─ 缩进呈现; 思维链以 dim 灰实时显示。
- 多行输入 (Enter 发送, Esc+Enter 换行), 命令历史落盘 ~/.qingxiaotuan/history/。
- 底部 footer 状态栏: 模型名 · 模式 · effort · 工作区。

降级: prompt_toolkit 不可用时自动退化为 readline 式单行输入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown

from ..logging_conf import log
from .. import __version__

# Kimi Code CLI 配色 token (dark 调色板, 青小团专属)
C = {
    "primary": "bright_blue",     # 最常用色: 标题 / 链接 / 选中 / 强调
    "accent": "cyan",             # 次级强调: ▶ 前缀 / 设备码框 / 提示
    "text": "bright_white",       # 正文
    "text_strong": "bold bright_white",  # 加粗强调
    "dim": "grey66",              # 次要 / 变暗文字
    "muted": "grey50",            # 最浅文字: footer / 计数
    "ok": "green",                # 成功: ✓ / 完成
    "warn": "yellow",             # 警告: auto/yolo 徽章
    "err": "red",                 # 错误 / 失败
    "branch": "grey50",           # 树形连接符 └─
    "box": "grey46",              # 边框线
}

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    _HAS_PT = True
except Exception:  # pragma: no cover - 极端环境降级
    _HAS_PT = False


_HELP = """\n可用斜杠命令 (Slash Commands)
────────────────────────────────────────
  /help      显示本帮助
  /tools     列出已注册工具
  /skills    列出已蒸馏技能
  /memory    查看长期记忆
  /model     显示当前模型 (含 /model switch 热切换 + planner/worker 角色模型)
  /effort    切换推理投入 (low/medium/high)
  /usage     查看本次会话 token 用量
  /context   显示上下文占用
  /status    查看当前工作区与 Agent 运行状态
  /index     重建代码库地图
  /loop      进入自主开发循环
  /parallel  并发派出子 Agent (a || b || c)
  /swarm     多 Agent 协作: 强模型规划/验收 + 弱模型并发 (共享黑板)
  /review    自动代码评审 (安全+逻辑+风格+复杂度)
  /route     智能模型路由 (根据任务难度选择模型)
  /cost      查看成本报告
  /clear     清空对话上下文
  /exit      退出

提示: Enter 发送 · Esc+Enter 换行 · 多行可用三引号"""


class UI:
    def __init__(self, console: Optional[Console] = None, home: Optional[Path] = None) -> None:
        self.console = console or Console()
        self.home = Path(home) if home else Path.home() / ".qingxiaotuan"
        self._session: Optional["PromptSession"] = None
        self._pt_disabled = not _HAS_PT
        self._reasoning_open = False
        # 吉祥物: 在 CLI 生命周期内维持状态, 由任务事件驱动切换
        from .mascot import Mascot, IDLE
        self._mascot = Mascot(IDLE)
        # 会话 token 统计 (由 stream/tool 调用时累加, /usage 可读)
        self._tokens = 0
        self._ctx_pct: Optional[float] = None

    # ------------------------------------------------------------ 输入

    def _build_session(self) -> Optional["PromptSession"]:
        if self._pt_disabled:
            return None
        try:
            hist = self.home / "history" / "repl.txt"
            hist.parent.mkdir(parents=True, exist_ok=True)
            kb = KeyBindings()

            @kb.add("enter")
            def _(event):  # Enter = 发送
                event.current_buffer.validate_and_handle()

            @kb.add("escape", "enter")
            def _(event):  # Esc+Enter = 换行, 支持多行输入
                event.current_buffer.insert_text("\n")

            return PromptSession(
                history=FileHistory(str(hist)),
                key_bindings=kb,
                multiline=True,
                prompt_continuation="  ",
                bottom_toolbar="[dim]Enter 发送 · Esc+Enter 换行 · /help[/]",
                enable_history_search=True,
            )
        except Exception:  # 非真实控制台等环境: 降级为 input()
            self._pt_disabled = True
            return None

    def prompt(self, prefix: str = "▶ ") -> str:
        """读取一行 (可能多行) 用户输入。返回去除首尾空白的文本。"""
        if self._session is None:
            self._session = self._build_session()
        if self._session is None:
            try:
                return input(prefix).strip()
            except (EOFError, KeyboardInterrupt):
                raise
        text = self._session.prompt(prefix)
        return text.strip()

    # ------------------------------------------------------------ 输出

    def banner(self, config, workspace: str, model: str, profile: str, mode: str = "standard",
               effort: str = "high") -> None:
        """青小团启动 banner: 左侧吉祥物 + 右侧信息盒 (Kimi Code CLI 风格)。"""
        from .mascot import Mascot, IDLE

        yolo = (mode == "yolo")
        mode_badge = "yolo · 自动批准" if yolo else "standard · 需确认"
        title = f"青小团 CLI"
        sub = f"v{__version__}"
        box, primary, accent, dim, muted = C["box"], C["primary"], C["accent"], C["dim"], C["muted"]
        inner_w = 56
        bar = "─" * inner_w

        m = Mascot(IDLE)
        art = m.ascii(0).split("\n")  # 3 行小图
        pad = max(len(a) for a in art)
        art = [a.ljust(pad) for a in art]

        self.console.print(f"[{box}]╭─[{box}]{bar}[{box}]╮[/{box}]")
        self.console.print(
            f"[{box}]│[/{box}] {art[0]}  [{primary}]{title}[/{primary}] [{dim}]{sub}[/{dim}]"
            f"   [{accent}]{model}[/{accent}]  [{muted}]{mode_badge}[/{muted}]"
        )
        self.console.print(
            f"[{box}]│[/{box}] {art[1]}  [{dim}]输入任务开始对话 · /help 查看命令 · /loop 进入自主开发[/{dim}]"
        )
        self.console.print(f"[{box}]│[/{box}] {art[2]}")
        self.console.print(f"[{box}]╰─[{box}]{bar}[{box}]╯[/{box}]")
        self.status_bar(mode, effort, workspace)
        self.console.print()

    def status_bar(self, mode: str = "standard", effort: str = "high", workspace: str = "") -> None:
        """底部 footer 状态栏: 吉祥物状态 · 模式 · effort · token · 上下文 · 工作区。"""
        yolo = (mode == "yolo")
        mode_txt = "yolo" if yolo else "standard"
        mode_c = C["warn"] if yolo else C["muted"]
        # 吉祥物状态图标 (单字表情, 随任务推进变化)
        icon = {"idle": "◦", "thinking": "◍", "working": "●", "alert": "⚠", "done": "✓"}.get(
            self._mascot.state, "◦")
        icon_c = {"idle": C["dim"], "thinking": C["primary"], "working": C["ok"],
                  "alert": C["warn"], "done": C["accent"]}.get(self._mascot.state, C["dim"])
        # token 用量 (累计), 上下文占用百分比
        tok = f"tok={self._tokens}" if self._tokens else ""
        ctx = f"ctx={self._ctx_pct:.0f}%" if self._ctx_pct is not None else ""
        parts = [f"[{icon_c}]{icon}[/{icon_c}]", f"[{mode_c}]{mode_txt}[/{mode_c}]",
                 f"effort={effort}"]
        if tok:
            parts.append(tok)
        if ctx:
            parts.append(ctx)
        parts.append(workspace)
        self.console.print(f"[{C['muted']}]─ {' · '.join(parts)}[/{C['muted']}]")

    # ------------------------------------------------------------ 吉祥物驱动
    def mascot_set(self, state: str) -> None:
        """手动切换吉祥物状态 (供命令层/事件钩子调用)。"""
        self._mascot.set(state)

    def mascot_tick(self) -> None:
        """推进吉祥物动画帧 (在 spinner / 长任务期间周期调用)。"""
        self._mascot.tick()

    def add_tokens(self, n: int) -> None:
        self._tokens += n

    def set_context_pct(self, pct: float) -> None:
        self._ctx_pct = max(0.0, min(100.0, pct))

    def phase(self, text: str) -> None:
        self.console.print(f"[{C['primary']}]▶ {text}[/{C['primary']}]")

    def think_start(self) -> None:
        self._mascot.set("thinking")
        self.console.print(f"[{C['dim']}]⠋ 思考中…[/]", end="", highlight=False)

    def stream(self, tok: str) -> None:
        self._close_reasoning()
        self.console.print(tok, end="", highlight=False)

    def reason(self, text: str) -> None:
        if not self._reasoning_open:
            self.console.print(f"\n[{C['dim']}]⠒ [/]", end=" ", highlight=False)
            self._reasoning_open = True
        self.console.print(text, end="", highlight=False)

    def _close_reasoning(self) -> None:
        if self._reasoning_open:
            self.console.print()  # 思维链结束, 换行
            self._reasoning_open = False

    def tool_call(self, name: str, args: str) -> None:
        self._close_reasoning()
        self._mascot.set("working")
        self.console.print(f"  [{C['accent']}]▶[/{C['accent']}] [{C['primary']}]{name}[/{C['primary']}] [{C['dim']}]{args[:90]}[/{C['dim']}]", highlight=False)

    def tool_result(self, name: str, summary: str) -> None:
        self._close_reasoning()
        flat = summary.replace("\n", " ").strip()
        failed = any(k in flat for k in ("[错误]", "[已拒绝]", "Error", "error:", "Traceback", "exit=1", "失败"))
        icon = f"[{C['err']}]✗[/{C['err']}]" if failed else f"[{C['ok']}]✓[/{C['ok']}]"
        s = flat[:160] + f" …({len(summary)}字符)" if len(flat) > 160 else flat
        self.console.print(f"    [{C['branch']}]└─[/{C['branch']}] {icon} [{C['dim']}]{s}[/{C['dim']}]", highlight=False)

    def answer_start(self) -> None:
        self._close_reasoning()
        self.console.print(f"[{C['primary']}]青小团[/{C['primary']}] ⏵ ", end="")

    def answer_md(self, text: str) -> None:
        self._close_reasoning()
        self.console.print(Markdown(text or "(无输出)"))

    def report(self, title: str, body: str) -> None:
        self._close_reasoning()
        self.console.print()
        self.console.print(f"[{C['primary']}]{title}[/{C['primary']}]")
        self.console.print(f"[{C['branch']}]{'─' * len(title)}[/{C['branch']}]")
        self.console.print(Markdown(body))

    def ask(self, question: str, options: Optional[str] = None) -> str:
        self._close_reasoning()
        self.console.print()
        self.console.print(f"[{C['warn']}]? {question}[/{C['warn']}]")
        if options:
            self.console.print(f"[{C['dim']}]{options}[/{C['dim']}]")
        try:
            return self.prompt(f"[{C['accent']}]▶ [/{C['accent']}]")
        except (EOFError, KeyboardInterrupt):
            return "退出"

    def usage(self, u: dict) -> None:
        hit = u.get("prompt_cache_hit_tokens", 0)
        miss = u.get("prompt_cache_miss_tokens", 0)
        cache_text = ""
        if hit or miss:
            total = hit + miss
            rate = (hit / total * 100) if total else 0
            color = C["ok"] if rate >= 90 else (C["warn"] if rate >= 60 else C["err"])
            cache_text = f" · [bold {color}]缓存命中 {rate:.0f}%[/]"
        self.console.print(
            f"[{C['dim']}]⚡ token · 入 {u.get('prompt_tokens', 0)} / 出 {u.get('completion_tokens', 0)}{cache_text}[/]"
        )

    def context_bar(self, stats: dict) -> None:
        """上下文占用: ━━━━┤42%├━━━━━ 消息数 / 估算 token / 预算。"""
        self._close_reasoning()
        est = stats.get("estimated_tokens", 0)
        budget = stats.get("budget_tokens", 1) or 1
        pct = max(0, min(100, int(est / budget * 100)))
        filled = pct // 10
        bar = "━" * filled + "┤" + f"{pct}%" + "├" + "━" * (10 - filled)
        color = C["ok"] if pct < 60 else (C["warn"] if pct < 85 else C["err"])
        self.console.print(
            f"[{C['dim']}]ctx [bold {color}]{bar}[/] "
            f"{stats.get('messages', 0)} 消息 · {est}/{budget} tok · 保留 {stats.get('keep_recent', 0)}"
        )

    def dashboard(self, *, model: str, provider: str, workspace: str,
                  mode: str, effort: str, stats: dict, tool_count: int) -> None:
        """显示一次紧凑的运行态摘要，便于长会话中快速确认环境。"""
        self._close_reasoning()
        est = stats.get("estimated_tokens", 0)
        budget = stats.get("budget_tokens", 0)
        mode_text = "YOLO · 自动批准" if mode == "yolo" else "STANDARD · 需确认"
        self.console.print(
            f"[{C['box']}]┌─ Agent 状态 ───────────────────────────────────────┐[/{C['box']}]"
        )
        self.console.print(
            f"[{C['box']}]│[/{C['box']}] 模型  [{C['accent']}]{provider}/{model}[/{C['accent']}]"
            f"   模式 [{C['warn'] if mode == 'yolo' else C['muted']}]{mode_text}[/]"
        )
        self.console.print(
            f"[{C['box']}]│[/{C['box']}] 上下文 {est}/{budget} tok · {stats.get('messages', 0)} 消息"
            f"   工具 {tool_count} · effort={effort}"
        )
        self.console.print(f"[{C['box']}]│[/{C['box']}] 工作区 [{C['dim']}]{workspace}[/{C['dim']}]")
        self.console.print(
            f"[{C['box']}]└───────────────────────────────────────────────────┘[/{C['box']}]"
        )

    def error(self, msg: str) -> None:
        log.error(msg)
        self.console.print(f"[{C['err']}]✗ {msg}[/{C['err']}]")

    def info(self, msg: str) -> None:
        log.info(msg)
        self.console.print(f"[{C['dim']}]› {msg}[/{C['dim']}]")

    @property
    def help_text(self) -> str:
        return _HELP
