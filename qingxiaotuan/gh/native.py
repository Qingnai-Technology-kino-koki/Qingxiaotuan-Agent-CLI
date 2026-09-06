"""原生 GitHub CLI 绑定 —— 把 `qxt gh` 变成真正的`gh`调用 (原生体验)。

原则:
  * **子进程透传**: 找到本机 `gh`, 原样拼接 argv 执行, stdout/stderr 流式原样返回,
    退出码与 `gh` 一致, `--json` / `--color` / `GH_HOST` 等环境全部兼容。
  * **安全闸门**: 执行前必经 ``policy.classify`` 分级; 破坏性操作 (DELETE 等)
    在**执行前**硬性拒绝, 返回退出码 3, 永不派生进程。
  * **显式后端**: 无 `gh` 时提供安装指引, 并给出纯 REST 回退入口 (仅读命令)。
  可注入 ``runner`` 便于在无真实 gh 的环境做端到端测试。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any, Callable, List, Optional, Tuple

from .policy import Action, SafetyPolicy, classify, FORBIDDEN_HINT
from .audit import append_event as audit_append

# gh 缺失时返回的退出码 (qxt 约定)。
EXIT_FORBIDDEN = 3
EXIT_NO_GH = 4
EXIT_NEED_CONFIRM = 2
EXIT_ERROR = 1


def audit_sink_default(argv: List[str], decision: str, event: str, detail: str) -> None:
    """默认审计 sink: 写 ~/.qingxiaotuan/logs/gh-audit.log。"""
    audit_append(argv, decision, event, detail)


def _audit_mute(_argv: List[str], _decision: str, _event: str, _detail: str) -> None:
    """静默 sink: 不写盘, 用于测试。"""
    pass

# 受保护操作需要显式确认: 交互终端会提示 y/N; 非交互必须带 --confirm (qxt 专属)。
CONFIRM_FLAGS = ("--confirm", "--qxt-confirm")


class GHNotInstalledError(RuntimeError):
    pass


def _runner_default(argv: List[str], timeout: Optional[int] = 300, env: Optional[dict] = None) -> Tuple[int, str, str]:
    """默认: 直接派生 gh, 捕获输出 (text), 返回 (rc, stdout, stderr)。"""
    proc = subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, env=env)
    return proc.returncode, proc.stdout, proc.stderr


class NativeGH:
    """`qxt gh` 的原生后端。"""

    def __init__(
        self,
        runner: Optional[Callable[..., Tuple[int, str, str]]] = None,
        policy: Optional[SafetyPolicy] = None,
        gh_path: Optional[str] = None,
        env: Optional[dict] = None,
        prompter: Optional[Callable[[str], str]] = None,
        interactive: Optional[bool] = None,
        audit_sink: Optional[Callable[[List[str], str, str, str], None]] = None,
    ) -> None:
        self._runner = runner or _runner_default
        self.policy = policy or SafetyPolicy()
        # gh_path: None=自动探测; ""=显式禁用 (模拟无 gh 环境)。
        if gh_path is None:
            gh_path = shutil.which("gh")
        self._gh_path = gh_path or None
        self._env = env if env is not None else dict(os.environ)
        # 受保护操作的确认器: 默认基于是否交互终端; 测试可注入固定返回值。
        self._prompter = prompter or (lambda p: input(p + " "))
        self._interactive = interactive  # None=自动: 两端都可 tty 才算交互
        # 破坏性操作拦截审计
        # 审计落盘 sink: (argv, decision_str, event, detail) -> None。
        # 默认写到 ~/.qingxiaotuan/logs/gh-audit.log; 测试可注入空 sink 关闭。
        self._blocked_log: Optional[Callable[[List[str], Action], None]] = None
        self._audit_sink = audit_sink or audit_sink_default

    def _is_interactive(self) -> bool:
        if self._interactive is not None:
            return self._interactive
        try:
            return sys.stdin.isatty() and sys.stdout.isatty()
        except Exception:  # noqa: BLE001
            return False

    # ---- 后端探测 ----
    def has_gh(self) -> bool:
        return self._gh_path is not None

    def version(self) -> str:
        if not self._gh_path:
            return "gh 未安装"
        rc, out, err = self._runner([self._gh_path, "--version"])
        if rc == 0 and out:
            return out.strip().splitlines()[0]
        return "gh (版本不可用)"

    # ---- 对外主入口 ----
    def run(self, argv: List[str], *, stream: bool = True,
            label: str = "gh", _confirmed: Optional[bool] = None) -> Tuple[int, str, str]:
        """对一条 `gh` 指令: 分级 → 拦截/确认 → 派生。返回 (rc, out, err)。

        - SAFE       直接放行 (原生体验);
        - GUARDED    受保护: 交互终端提示 y/N; 非交互须带 ``--confirm`` (已剥离);
                     带 --confirm 或交互确认通过 → 放行, 否则退出码 2;
        - FORBIDDEN  破坏性: 硬性拒绝, 永不派生 (退出码 3)。
        """
        if not self._gh_path:
            raise GHNotInstalledError(_install_hint())

        had_confirm = any(a.lower() in CONFIRM_FLAGS for a in argv)
        argv = self._strip_confirm(argv)

        decision = classify(argv, self.policy)
        if decision is Action.FORBIDDEN:
            self._log_blocked(argv, decision)
            self._emit(argv, decision.value, "blocked", _refusal(argv))
            return EXIT_FORBIDDEN, "", _refusal(argv)

        if decision is Action.GUARDED:
            if self.policy.verbose_guarded:
                print(_guard_notice(argv), file=sys.stderr)
            allowed = _confirmed
            if allowed is None:
                allowed = had_confirm or self._ask_confirm(argv)
            if not allowed:
                self._emit(argv, decision.value, "cancelled", _cancelled(argv))
                return EXIT_NEED_CONFIRM, _cancelled(argv), ""
            self._emit(argv, decision.value, "guarded_passed", _guard_notice(argv))

        full = [self._gh_path] + list(argv)
        return self._runner(full)

    def _ask_confirm(self, argv: List[str]) -> bool:
        """受保护操作的真实确认: 交互则提问; 非交互一律拒绝 (须显式 --confirm)。"""
        if not self._is_interactive():
            return False  # 非交互: 只能靠 --confirm (已被上方 had_confirm 捕获)
        try:
            reply = self._prompter(f"确认执行: gh {' '.join(argv)}\n[y/N]")
        except (EOFError, KeyboardInterrupt):
            return False
        return reply.strip().lower() in ("y", "yes", "y")

    @staticmethod
    def _strip_confirm(argv: List[str]) -> List[str]:
        return [a for a in argv if a.lower() not in CONFIRM_FLAGS]

    def _log_blocked(self, argv: List[str], decision: Action) -> None:
        """破坏性拦截写审计日志 (可观测; 不阻断拦截本身)。"""
        if self._blocked_log is None:
            return
        try:
            self._blocked_log(list(argv), decision)
        except Exception:  # noqa: BLE001 - 审计失败不影响安全拦截
            pass

    def _emit(self, argv: List[str], decision: str, event: str, detail: str) -> None:
        """统一审计出口: 落盘 / 可观测。写盘失败不影响安全判定。"""
        try:
            self._audit_sink(list(argv), decision, event, detail)
        except Exception:  # noqa: BLE001
            pass

    # ---- 便捷只读方法 (无 gh 时走 REST 回退) ----
    def gh_api(self, path: str, params: Optional[dict] = None,
               method_preference: Optional[str] = None) -> Tuple[int, str, str]:
        """`gh api <path>` 只读访问的便捷封装。返回 (rc, out, err)。"""
        argv = ["api", path]
        if method_preference and method_preference not in ("GET", "HEAD"):
            # 只读便捷方法绝不注入破坏性 method
            method_preference = None
        if method_preference:
            argv += ["-X", method_preference]
        if params:
            for k, v in params.items():
                argv += ["-f", f"{k}={v}"]
        argv += ["-H", "Accept: application/vnd.github+json"]
        return self.run(argv, stream=False)


def _refusal(argv: List[str]) -> str:
    cmd = " gh " + " ".join(argv)
    return f"[安全拦截] 拒绝执行: {cmd}\n{FORBIDDEN_HINT}"


def _cancelled(argv: List[str]) -> str:
    cmd = "gh " + " ".join(argv)
    return f"[已取消] 受保护操作未获确认, 已停止: {cmd}\n补充: 交互终端会提示 y/N; 非交互环境请显式追加 --confirm。"


def _guard_notice(argv: List[str]) -> str:
    cmd = "gh " + " ".join(argv)
    return f"[提示] 受保护操作 (远端状态变更): {cmd}"


def _install_hint() -> str:
    return (
        "未检测到本机 `gh` CLI。\n"
        "安装: winget install GitHub.cli   (Windows)\n"
        "      brew install gh              (macOS)\n"
        "      snap install gh --classic     (Linux)\n"
        "之后用 `qxt gh auth login` 认证即可获得原生体验。\n"
        "只读命令可加 `--rest` 走内置 REST 回退。"
    )


def build_argv(cmd: str) -> List[str]:
    """把一行 ``gh ...`` 命令切分成 argv (简单健壮的分词, 兼容引号)。"""
    import shlex
    return shlex.split(cmd, posix=os.name != "nt") if cmd.strip() else []