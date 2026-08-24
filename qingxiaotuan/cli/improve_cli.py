"""`qxt improve` 子命令 —— 自我改进闭环的命令行入口。

子命令:
  qxt improve summarize   读取内核事件流, 预览将从历史中提炼的经验与规则 (dry-run, 不落盘)
  qxt improve apply       把经验固化为 rules .jsonl + 技能草稿, 并回报落盘路径
"""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from ..app import build_kernel
from ..self_improve.plugin import SelfImprovePlugin

console = Console()


def _load_service(args) -> object:
    kernel = build_kernel(profile=getattr(args, "profile", "default"),
                           patch_file=getattr(args, "patch", None))
    # 确保插件已激活 (build_kernel 已 activate_all)
    svc = kernel.get("self_improve")
    if svc is None:
        # 兜底: 手动注册激活
        kernel.register(SelfImprovePlugin())
        kernel.activate_all()
        svc = kernel.get("self_improve")
    return svc, kernel


def cmd_improve(args) -> int:
    svc, kernel = _load_service(args)
    if svc is None:
        console.print("[red]self_improve 服务不可用。[/]")
        return 1
    sub = getattr(args, "improve_cmd", None)
    if sub == "apply":
        return _do_apply(svc)
    return _do_summarize(svc)


def _do_summarize(svc) -> int:
    data = svc.summarize()
    n = data.get("total_experiences", 0)
    console.print(f"[bold cyan]自我改进复盘[/] — 共抽取 [yellow]{n}[/] 条经验\n")
    exps = data.get("experiences", [])
    if exps:
        t = Table(title="经验", show_lines=False)
        t.add_column("类型", style="magenta")
        t.add_column("工具", style="cyan")
        t.add_column("频次", style="green")
        t.add_column("摘要", style="dim")
        for e in exps:
            t.add_row(e.get("kind", "?"), e.get("tool", "?"), str(e.get("count", 1)), e.get("summary", ""))
        console.print(t)
    else:
        console.print("[dim]（暂无经验 —— 多跑几次任务后这里会积累可改进点）[/]")
    console.print("\n[bold]将生成的规则预览:[/]")
    console.print(data.get("rule_preview", "（无）"))
    if data.get("skill_draft_candidates"):
        console.print(f"\n[bold]技能草稿候选:[/] {', '.join(data['skill_draft_candidates'])}")
    console.print("\n[dim]以上为 dry-run, 执行 `qxt improve apply` 才会落盘。[/]")
    return 0


def _do_apply(svc) -> int:
    data = svc.apply()
    console.print("[bold green]已应用自我改进[/]\n")
    if data.get("rules_written"):
        console.print(f"[green]规则文件:[/] {data['rules_written']} ({data['rules_count']} 条)")
    else:
        console.print("[dim]规则: 无新规则生成[/]")
    drafts = data.get("skill_drafts") or []
    if drafts:
        console.print(f"[green]技能草稿:[/] {len(drafts)} 个")
        for p in drafts:
            console.print(f"  - {p}")
        console.print("[yellow]技能草稿为 DRAFT 状态, 需人工审阅后启用。[/]")
    else:
        console.print("[dim]技能: 无新草稿[/]")
    console.print(f"\n[dim]基于 {data.get('experiences', 0)} 条经验。[/]")
    return 0
