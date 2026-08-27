"""Shell 工具插件: 在工作区执行终端命令 (危险操作, 默认需用户确认)。

世界级差异化: 命令执行前先经「最小影响半径」安全护栏 (safety 引擎) 做静态评分。
    - critical 级: 默认硬拦截, 除非 YOLO 且不在红线名单 (绝不自动执行 rm -rf / force push 等)
    - high / medium 级: 在确认提示中展示安全替代建议, 由用户决策
    - none / low: 正常放行
护栏引擎不可用时普通命令安全降级放行, 但 YOLO 红线判定本地兜底, 依然拦截致命命令。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from ..core.kernel import Kernel, Plugin
from ..ext.safety_engine import is_redline as _engine_is_redline
from .base import Tool, ToolContext, string_prop

log = logging.getLogger(__name__)

MAX_OUTPUT = 20000

# 字面量兜底 (展示用/兼容旧引用); 判定逻辑以 ext/safety_engine.is_redline 为单一来源。
# 即使 YOLO 模式也绝不自动执行的致命操作红线 (与 safety 引擎的 critical 规则对齐)
YOLO_REDLINE = (
    "rm -rf", "rm -fr", "del /s /q", "rmdir /s", "format ", "sudo rm",
    "push --force", "push -f", "drop table", "drop database", "fork bomb",
    ":(){",
)


def is_redline(command: str) -> bool:
    """致命操作红线: 引擎 token 化判定 (单一来源) + 字面量兜底。

    覆盖 rm -rf / rm -r -f / --recursive --force / git push -f / del /s 等变体。"""
    c = command.lower()
    if any(tok in c for tok in YOLO_REDLINE):
        return True
    try:
        return _engine_is_redline(c)
    except Exception as exc:  # noqa: BLE001
        log.debug("redline 引擎判定异常, 回退字面量: %s", exc)
        return False

# Plan 模式下的写操作特征: 命中任一即视为修改类命令, 只读模式拦截
_WRITE_MARKERS = (
    ">", ">>", "| tee", "sed -i", "rm ", "mv ", "cp ", "mkdir", "touch ", "chmod", "chown",
    "git add", "git commit", "git push", "git reset", "git checkout", "git clean",
    "git stash", "git merge", "git rebase", "git tag", "git branch -d", "git branch -D",
    "npm install", "npm i ", "npm run build", "pip install", "pip uninstall",
    "pipenv install", "poetry add", "poetry install", "cargo build", "cargo install",
    "go build", "go install", "go mod", "make ", "cmake", "docker build", "docker run",
    "docker compose", "kubectl apply", "terraform apply", "python -m pytest --cov",
    "pytest --cov", "coverage run", "black ", "isort ", "ruff --fix", "yarn add",
    "pnpm add", "bun add", "conda install", "apt install", "apt-get install",
    "brew install", "brew uninstall", "dd ", "mkfs", "fdisk", "kill ", "pkill",
    "taskkill", "del ", "erase ", "-delete", "ren ", "copy ", "xcopy", "robocopy", "move ",
    "curl -o", "wget -O", "wget -o", "tar -x", "unzip", "git init", "git clone",
    "git config", "git remote", "git fetch", "git pull",
)


def _is_readonly_command(command: str) -> bool:
    """判断命令是否只读 (Plan 模式放行)。

    fail-closed 语义: 写特征命中 → 拒绝; 只读前缀命中 → 放行;
    无法识别的命令一律视为修改类 (保守拒绝), 宁可多拦不误放。

    示例:
      ls -la                          → True  (只读前缀)
      cat file                        → True  (只读前缀)
      cat file | sed -i 's/a/b/'      → False (管道含写特征 sed -i)
      echo x > file                   → False (写特征 >)
      grep -r pattern .               → True  (只读前缀 grep)
      python -m pytest --cov          → False (写特征 pytest --cov)
    """
    c = command.strip().lower()
    if not c:
        return True
    # ① 写特征优先: 命中任一即视为修改类 (覆盖全串, 包括管道下游)
    #    即使以 cat/echo 等只读前缀开头 (如 cat file | sed -i)
    if any(marker in c for marker in _WRITE_MARKERS):
        return False
    # ② 纯管道/查看类命令: 检查管道每一段的首词, 任一含写特征已在 ① 拦截
    first = c.split("|")[0].strip()
    readonly_prefixes = ("ls", "cat", "head", "tail", "grep", "find", "echo",
                         "pwd", "whoami", "date", "which", "where", "type",
                         "git status", "git diff", "git log", "git show",
                         "git branch", "git remote -v", "python -c", "python -m py_compile")
    return any(first.startswith(p) for p in readonly_prefixes)


def _risk_to_advice(reasons: list, previews: list) -> str:
    parts = []
    if reasons:
        parts.append("命中风险: " + "; ".join(reasons[:3]))
    if previews:
        parts.append("安全替代: " + " | ".join(previews[:2]))
    return "  ".join(parts)


def _pre_exec_guard(ctx: ToolContext, command: str) -> str | None:
    """执行前安全拦截。返回 None 表示放行; 返回字符串表示拦截原因 (已拒绝)。"""
    # YOLO 红线优先于引擎可用性: 即便 safety 服务缺失也绝不放行致命命令 (fail-closed)
    if getattr(ctx, "yolo", False) and is_redline(command):
        reasons: list[str] = []
        previews: list[str] = []
        svc = ctx.kernel.get("safety_check") if ctx.kernel else None
        if svc is not None:
            try:
                verdict = svc.check(ctx, command)
                reasons = verdict.get("reasons") or []
                previews = verdict.get("safe_preview") or []
            except Exception as exc:  # noqa: BLE001
                log.debug("safety_check 调用失败 (红线拦截不受影响): %s", exc)
        return (f"[已拦截] 命中致命操作红线, 即使在 YOLO 模式也禁止自动执行: {command}\n"
                + _risk_to_advice(reasons, previews))
    if ctx.kernel is None:
        return None
    svc = ctx.kernel.get("safety_check")
    if svc is None:
        return None
    try:
        verdict = svc.check(ctx, command)
    except Exception as exc:  # noqa: BLE001
        log.debug("safety_check 异常, 安全降级放行: %s", exc)
        return None  # 引擎异常, 安全降级放行
    risk = (verdict.get("risk") or "none").lower()
    block = bool(verdict.get("block"))
    reasons = verdict.get("reasons") or []
    previews = verdict.get("safe_preview") or []
    # critical 默认硬拦截; 非 YOLO 且存在确认通道时允许人工放行一次。
    # YOLO 下维持硬拦 (fail-closed): 自动模式不给致命操作开口子。
    if block or risk == "critical":
        advice = _risk_to_advice(reasons, previews)
        if not getattr(ctx, "yolo", False) and ctx.confirm is not None:
            warning = (
                f"⚠️ 检测到致命风险操作 (critical):\n{command}\n{advice}\n"
                "安全引擎建议不要执行。确认要人工放行吗? (仅本次生效)"
            )
            try:
                if ctx.confirm(warning):
                    return None  # 人工显式放行, 仅本次有效
            except Exception as exc:  # noqa: BLE001
                log.debug("critical 人工放行确认异常, 维持拦截: %s", exc)
        suffix = "" if ctx.confirm is not None else "\n(当前无确认通道, 无法请求人工放行)"
        return (f"[已拦截] 检测到致命风险操作 (critical), 必须人工确认, 不自动执行:\n{command}\n"
                + advice + suffix)
    # high / medium: 把安全建议挂到 ctx, 由确认流程展示
    if risk in ("high", "medium") and (reasons or previews):
        ctx.safety_advice = _risk_to_advice(reasons, previews)
    return None


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    """终止进程树 (Windows 用 taskkill /T, POSIX 用 killpg)。"""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=5,
            )
        else:
            import os, signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception:  # noqa: BLE001
        # 兜底: 至少杀主进程
        try:
            proc.kill()
        except Exception:
            pass


def run_shell(ctx: ToolContext, command: str, timeout: int = 0) -> str:
    # 0) Plan 模式: 只读命令放行, 写命令拦截 (对标 Claude Code Plan Mode)
    if getattr(ctx, "plan_mode", False) and not _is_readonly_command(command):
        return ("[Plan 模式] 只读模式已启用, 已阻止修改类命令:\n"
                f"  {command}\n"
                "请先输出分析与实施计划, 退出 Plan 模式后再执行修改。")
    # 1) 执行前安全护栏 (最小影响半径)
    guard = _pre_exec_guard(ctx, command)
    if guard:
        return guard
    limit = timeout or ctx.config("tools.shell.timeout", 60)
    try:
        # POSIX 下让子进程独占进程组, 超时才能安全整树 kill (killpg 不误伤自身); Windows 忽略该参数
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=ctx.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=os.name != "nt",
        )
        try:
            if limit and limit > 0:
                out, err = proc.communicate(timeout=limit)
            else:
                out, err = proc.communicate()
        except subprocess.TimeoutExpired:
            # 超时: 只杀原进程树, 返回部分输出 (而非让异常炸掉 Agent 循环)。
            # 注意绝不能重新执行命令 —— 那等于把超时任务再跑一遍。
            _kill_proc_tree(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                out, err = "", ""
            partial = ((out or "") + (err or "")).strip()
            if len(partial) > MAX_OUTPUT:
                partial = partial[:MAX_OUTPUT] + f"\n...[截断, 原始输出 {len(partial)} 字符]"
            notice = "(命令超时, 进程树已被终止)"
            return f"exit=-1\n{notice}\n{partial}" if partial else f"exit=-1\n{notice}"
        out = (out or "") + (err or "")
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + f"\n...[截断, 原始输出 {len(out)} 字符]"
        return f"exit={proc.returncode}\n{out.strip()}"
    except Exception as exc:  # noqa: BLE001
        return f"[错误] shell 执行异常: {type(exc).__name__}: {exc}"


class ShellPlugin(Plugin):
    name = "tools.shell"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("tools.shell.enabled", True):
            return
        registry = kernel.require("tool_registry")
        need_confirm = not config or config.get("tools.shell.require_confirm", True)
        registry.register(Tool(
            name="run_shell",
            description="在工作区执行 shell 命令, 返回退出码与输出。执行前经最小影响半径安全护栏静态评分, critical 级自动拦截。",
            parameters={
                "type": "object",
                "properties": {
                    "command": string_prop("命令"),
                    "timeout": {"type": "integer", "description": "超时秒数 (可选)"},
                },
                "required": ["command"],
            },
            handler=run_shell,
            dangerous=need_confirm,
            group="shell",
        ))
