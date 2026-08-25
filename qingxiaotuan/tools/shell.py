"""Shell 工具插件: 在工作区执行终端命令 (危险操作, 默认需用户确认)。

世界级差异化: 命令执行前先经「最小影响半径」安全护栏 (safety C 引擎) 做静态评分。
    - critical 级: 默认硬拦截, 除非 YOLO 且不在红线名单 (绝不自动执行 rm -rf / force push 等)
    - high / medium 级: 在确认提示中展示安全替代建议, 由用户决策
    - none / low: 正常放行
护栏引擎不可用时 (未编译) 安全降级, 不阻断正常命令执行。
"""

from __future__ import annotations

import subprocess

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop

MAX_OUTPUT = 20000

# 即使 YOLO 模式也绝不自动执行的致命操作红线 (与 safety 引擎的 critical 规则对齐)
YOLO_REDLINE = (
    "rm -rf", "rm -fr", "del /s /q", "rmdir /s", "format ", "sudo rm",
    "push --force", "push -f", "drop table", "drop database", "fork bomb",
    ":(){",
)

# Plan 模式下的写操作特征: 命中任一即视为修改类命令, 只读模式拦截
_WRITE_MARKERS = (
    ">", ">>", "| tee", "rm ", "mv ", "cp ", "mkdir", "touch ", "chmod", "chown",
    "git add", "git commit", "git push", "git reset", "git checkout", "git clean",
    "git stash", "git merge", "git rebase", "git tag", "git branch -d", "git branch -D",
    "npm install", "npm i ", "npm run build", "pip install", "pip uninstall",
    "pipenv install", "poetry add", "poetry install", "cargo build", "cargo install",
    "go build", "go install", "go mod", "make ", "cmake", "docker build", "docker run",
    "docker compose", "kubectl apply", "terraform apply", "python -m pytest --cov",
    "pytest --cov", "coverage run", "black ", "isort ", "ruff --fix", "yarn add",
    "pnpm add", "bun add", "conda install", "apt install", "apt-get install",
    "brew install", "brew uninstall", "dd ", "mkfs", "fdisk", "kill ", "pkill",
    "taskkill", "del ", "erase ", "ren ", "copy ", "xcopy", "robocopy", "move ",
    "curl -o", "wget -O", "wget -o", "tar -x", "unzip", "git init", "git clone",
    "git config", "git remote", "git fetch", "git pull",
)


def _is_readonly_command(command: str) -> bool:
    """判断命令是否只读 (Plan 模式放行)。保守判断: 命中写特征即视为修改类。"""
    c = command.strip().lower()
    if not c:
        return True
    # 写特征优先: 命中任一即视为修改类 (即使以 echo/ls 等只读前缀开头, 如 echo x > file)
    if any(marker in c for marker in _WRITE_MARKERS):
        return False
    # 纯管道/查看类命令开头
    first = c.split("|")[0].strip()
    readonly_prefixes = ("ls", "cat", "head", "tail", "grep", "find", "echo",
                         "pwd", "whoami", "date", "which", "where", "type",
                         "git status", "git diff", "git log", "git show",
                         "git branch", "git remote -v", "python -c", "python -m py_compile")
    if any(first.startswith(p) for p in readonly_prefixes):
        return True
    return True


def _risk_to_advice(reasons: list, previews: list) -> str:
    parts = []
    if reasons:
        parts.append("命中风险: " + "; ".join(reasons[:3]))
    if previews:
        parts.append("安全替代: " + " | ".join(previews[:2]))
    return "  ".join(parts)


def _pre_exec_guard(ctx: ToolContext, command: str) -> str | None:
    """执行前安全拦截。返回 None 表示放行; 返回字符串表示拦截原因 (已拒绝)。"""
    if ctx.kernel is None:
        return None
    svc = ctx.kernel.get("safety_check")
    if svc is None:
        return None
    try:
        verdict = svc.check(ctx, command)
    except Exception:  # noqa: BLE001
        return None  # 引擎异常, 安全降级放行
    risk = (verdict.get("risk") or "none").lower()
    block = bool(verdict.get("block"))
    reasons = verdict.get("reasons") or []
    previews = verdict.get("safe_preview") or []
    # YOLO 红线: 即便 yolo 也绝不自动执行
    if getattr(ctx, "yolo", False) and any(tok in command.lower() for tok in YOLO_REDLINE):
        return (f"[已拦截] 命中致命操作红线, 即使在 YOLO 模式也禁止自动执行: {command}\n"
                + _risk_to_advice(reasons, previews))
    # critical 默认硬拦截 (需人工显式确认, 不依赖 yolo)
    if block or risk == "critical":
        return (f"[已拦截] 检测到致命风险操作 (critical), 必须人工确认, 不自动执行:\n{command}\n"
                + _risk_to_advice(reasons, previews))
    # high / medium: 把安全建议挂到 ctx, 由确认流程展示
    if risk in ("high", "medium") and (reasons or previews):
        ctx.safety_advice = _risk_to_advice(reasons, previews)
    return None


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
    proc = subprocess.run(
        command,
        shell=True,
        cwd=ctx.workspace,
        capture_output=True,
        text=True,
        timeout=limit,
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n...[截断, 原始输出 {len(out)} 字符]"
    return f"exit={proc.returncode}\n{out.strip()}"


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
