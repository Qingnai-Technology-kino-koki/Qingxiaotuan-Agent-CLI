"""qxt tutorial —— 任务驱动的内置教程 (onboarding 核心能力)。

让新人用真实命令「做成一件事、感受到价值」，而不是被功能列表淹没。

    qxt tutorial list                      列出可用教程
    qxt tutorial run <name>                逐步引导 (默认只展示, 不自动执行)
    qxt tutorial run <name> --exec         同时执行标注为安全的演示命令
    qxt tutorial run <name> --step N       从指定步骤开始
    qxt tutorial run <name> --no-pause     步骤间不暂停

教程内容来自 qingxiaotuan/resources/tutorials/*.yaml。
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import yaml
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from ..ui.plain_console import console

_TUTORIAL_DIR = Path(__file__).resolve().parent.parent / "resources" / "tutorials"


def _read_tutorials():
    """读取 tutorials 目录下的全部 YAML, 返回 dict 列表 (按 name 去重)。"""
    if not _TUTORIAL_DIR.exists():
        return []
    out = []
    for f in sorted(_TUTORIAL_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception as exc:  # 单个坏文件不应拖垮整个 list
            console.print(f"[yellow]跳过无法解析的教程 {f.name}: {exc}[/yellow]")
            continue
        if isinstance(data, dict) and data.get("name"):
            out.append(data)
    return out


def _print_list() -> int:
    tuts = _read_tutorials()
    if not tuts:
        console.print("[yellow]未找到任何教程。[/yellow]")
        return 0
    from ..ui.format import Table

    table = Table(title="内置教程 (qxt tutorial run <name>)")
    table.add_column("名称")
    table.add_column("难度")
    table.add_column("标题")
    table.add_column("简介")
    for t in tuts:
        table.add_row(
            t.get("name", "?"),
            str(t.get("level", "-")),
            t.get("title", ""),
            t.get("description", ""),
        )
    console.print(table)
    console.print("\n运行: [cyan]qxt tutorial run <名称>[/cyan]   加 [cyan]--exec[/cyan] 可同时执行安全演示命令")
    return 0


# 这些字符出现时回退到 shell=True (我们的演示命令都不含, 故默认用 shlex 解析, 跨平台安全)
_SHELL_META = set("|&;><()`")


def _exec_demo(cmd: str) -> None:
    """执行标注为安全的演示命令 (仅 demo: true 的步骤会走到这里)。

    用 shlex 解析 + shell=False, 避免 Windows cmd.exe 对单/双引号 JSON 参数的
    经典引号处理 bug; 仅当命令含管道/重定向等 shell 元字符时才回退到 shell=True。
    """
    argv = None
    try:
        argv = shlex.split(cmd)
    except Exception:
        argv = None
    use_shell = argv is None or any(c in cmd for c in _SHELL_META)
    try:
        if use_shell:
            r = subprocess.run(cmd, shell=True, timeout=120)
        else:
            r = subprocess.run(argv, shell=False, timeout=120)
        if r.returncode != 0:
            console.print(f"[yellow]演示命令返回非零退出码: {r.returncode}[/yellow]")
    except Exception as exc:  # 演示失败不应中断教程
        console.print(f"[yellow]演示执行失败: {exc}[/yellow]")


def _run_tutorial(name: str, exec_demo: bool, start_step: int, no_pause: bool) -> int:
    by_name = {t["name"]: t for t in _read_tutorials()}
    if name not in by_name:
        console.print(f"[red]未找到教程: {name}[/red]")
        console.print("可用: " + (", ".join(by_name) or "(无)"))
        return 2

    t = by_name[name]
    steps = t.get("steps") or []
    console.print(
        Panel(
            f"[bold]{t.get('title', '')}[/bold]\n\n{t.get('description', '')}",
            title=f"教程 · {name}",
            border_style="cyan",
        )
    )

    for i, step in enumerate(steps, start=1):
        if i < start_step:
            continue
        console.print()
        console.print(Panel(f"[bold]{i}. {step.get('title', '')}[/bold]", border_style="blue"))
        narr = step.get("narrative")
        if narr:
            console.print(Markdown(narr.strip()))
        try_cmd = step.get("try")
        if try_cmd:
            console.print("\n[bold cyan]试一试:[/bold cyan]")
            console.print(Panel(try_cmd.strip(), border_style="green"))
            note = step.get("note")
            if note:
                console.print(f"[dim]{note}[/dim]")
            if exec_demo and step.get("demo"):
                console.print("\n[dim]— 执行安全演示 —[/dim]")
                _exec_demo(try_cmd.strip())
        if not no_pause and i < len(steps):
            ans = Prompt.ask("回车继续下一步 (q 退出)", default="", show_default=False)
            if ans.strip().lower() in ("q", "quit", "exit"):
                break

    console.print("\n[green]教程结束。[/green] 更多命令: [cyan]/help[/cyan] 或 [cyan]qxt tutorial list[/cyan]")
    return 0


def cmd_tutorial(args) -> int:
    sub = getattr(args, "tutorial_cmd", None)
    if sub == "run":
        name = getattr(args, "name", None)
        if not name:
            console.print("[red]请指定教程名称: qxt tutorial run <name>[/red]")
            return 2
        return _run_tutorial(
            name,
            getattr(args, "exec_demo", False),
            getattr(args, "step", 1) or 1,
            getattr(args, "no_pause", False),
        )
    # list 或空 (默认列出)
    return _print_list()
