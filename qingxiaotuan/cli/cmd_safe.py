"""qxt safe —— 安全总入口 (整合白名单 / 本地黑名单减负 / 状态 / 更新)。

把原先分散的 ``qxt security`` 与 ``qxt whitelist`` 收敛为一个简洁入口, 降低上手难度:

  qxt safe                      安全总览 (等同 status)
  qxt safe status               安全系统状态 + 审计概览
  qxt safe list                 列出白名单
  qxt safe allow <命令>         把命令加入白名单 (用户在本地自主添加)
  qxt safe deny <命令>          从白名单移除
  qxt safe blacklist            列出内置黑名单模式 (标注已减负)
  qxt safe reduce <关键字>      抑制匹配关键字的黑名单模式 (本地减负)
  qxt safe restore <关键字>     恢复被抑制的黑名单模式
  qxt safe update               更新安全策略 (月度检查)
"""

from __future__ import annotations

from typing import Any

from ..config.loader import home_dir
from ._ui_singleton import console


# ---------------------------------------------------------------- 分发入口

def cmd_safe(args) -> int:
    """安全总入口命令。"""
    safe_cmd = getattr(args, "safe_cmd", None) or "status"

    if safe_cmd == "status":
        return _cmd_status()
    elif safe_cmd == "list":
        return _cmd_whitelist_list()
    elif safe_cmd == "allow":
        return _cmd_whitelist_add(args)
    elif safe_cmd == "deny":
        return _cmd_whitelist_remove(args)
    elif safe_cmd == "blacklist":
        return _cmd_blacklist()
    elif safe_cmd == "reduce":
        return _cmd_reduce(args)
    elif safe_cmd == "restore":
        return _cmd_restore(args)
    elif safe_cmd == "update":
        return _cmd_update()
    else:
        console.print(f"[red]未知子命令: {safe_cmd}[/red]")
        console.print("可用: status / list / allow / deny / blacklist / reduce / restore / update")
        return 1


# ---------------------------------------------------------------- 状态总览

def _cmd_status() -> int:
    """安全系统状态 + 审计概览。"""
    console.print("\n[bold]=== 青小团 · 安全总览 ===[/bold]\n")

    # 白名单
    try:
        from ..core.whitelist import WhitelistManager
        wl = WhitelistManager()
        entries = wl.list()
        console.print(f"[bold]白名单[/bold]: {len(entries)} 条 (存储于 {wl._path})")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]白名单读取失败: {exc}[/red]")

    # 黑名单减负
    try:
        from ..core import blacklist_override
        suppressed = blacklist_override.list_suppressed()
        if suppressed:
            console.print(f"[bold]黑名单减负[/bold]: 已抑制 {len(suppressed)} 个关键字 -> "
                          f"{', '.join(suppressed)}")
        else:
            console.print("[bold]黑名单减负[/bold]: 未启用 (内置黑名单全部生效)")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]黑名单减负读取失败: {exc}[/red]")

    # 安全引擎
    try:
        from ..ext.safety_engine import SafetyEngine
        methods = SafetyEngine().list_methods()
        console.print(f"[bold]安全引擎[/bold]: v{methods.get('version', '?')} "
                      f"能力 {', '.join(methods.get('capabilities', []))}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]安全引擎读取失败: {exc}[/red]")

    # 审计概览
    try:
        from ..core.security_bus import get_security_bus
        bus = get_security_bus()
        stats = bus.get_stats()
        console.print(f"[bold]审计[/bold]: 累计 {stats.get('total_events', 0)} 条事件 "
                      f"(critical {stats.get('by_severity', {}).get('critical', 0)})")
        console.print(f"    落盘: {bus._persist_path or '(未持久化, 仅内存)'}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]审计读取失败: {exc}[/red]")

    # 月度更新
    try:
        from ..ext.security_policy import check_monthly_update
        upd = check_monthly_update()
        if upd.get("due"):
            console.print(f"[yellow]⚠️ {upd.get('message', '安全策略待更新')}[/yellow]")
        else:
            console.print(f"[green]✓ {upd.get('message', '安全策略已是最新')}[/green]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[dim]更新检查跳过: {exc}[/dim]")

    console.print("\n[dim]完整明细: qxt safe blacklist / qxt safe list / qxt safe update[/dim]")
    return 0


# ---------------------------------------------------------------- 白名单 (用户在本地自主添加)

def _cmd_whitelist_list() -> int:
    try:
        from ..core.whitelist import WhitelistManager
        wl = WhitelistManager()
        entries = wl.list()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]白名单读取失败: {exc}[/red]")
        return 1

    if not entries:
        console.print("[yellow]白名单为空 (用 qxt safe allow <命令> 添加)[/yellow]")
        return 0

    console.print(f"\n[bold]白名单条目 ({len(entries)} 个):[/bold]\n")
    for i, entry in enumerate(entries, 1):
        cmd = entry.get("command", "")
        desc = entry.get("description", "")
        console.print(f"  {i:2d}. {cmd:<20s} {desc}")
    return 0


def _cmd_whitelist_add(args) -> int:
    command = getattr(args, "command", None)
    if not command:
        console.print("[red]用法: qxt safe allow <命令>[/red]")
        return 1
    description = getattr(args, "description", "") or "用户本地自主添加"

    try:
        from ..ext.safety_engine import is_redline
        from ..core.whitelist import WhitelistManager
        if is_redline(command):
            console.print(f"[red]红线命令无法加入白名单: {command}[/red]")
            return 1
        wl = WhitelistManager()
        if wl.add(command, description):
            console.print(f"[green]已加入白名单: {command}[/green]")
        else:
            console.print(f"[yellow]已在白名单中: {command}[/yellow]")
        return 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]添加失败: {exc}[/red]")
        return 1


def _cmd_whitelist_remove(args) -> int:
    command = getattr(args, "command", None)
    if not command:
        console.print("[red]用法: qxt safe deny <命令>[/red]")
        return 1
    try:
        from ..core.whitelist import WhitelistManager
        wl = WhitelistManager()
        if wl.remove(command):
            console.print(f"[green]已从白名单移除: {command}[/green]")
        else:
            console.print(f"[yellow]不在白名单中: {command}[/yellow]")
        return 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]移除失败: {exc}[/red]")
        return 1


# ---------------------------------------------------------------- 本地黑名单减负

def _cmd_blacklist() -> int:
    """列出内置黑名单模式库, 标注哪些已被用户本地抑制。"""
    try:
        from ..core import blacklist_override
        from ..ext.safety_engine import SafetyEngine
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]黑名单读取失败: {exc}[/red]")
        return 1

    engine = SafetyEngine()
    levels = [
        ("CRITICAL (致命)", engine.CRITICAL_PATTERNS),
        ("HIGH (高危)", engine.HIGH_PATTERNS),
        ("MEDIUM (中危)", engine.MEDIUM_PATTERNS),
    ]

    console.print("\n[bold]内置黑名单模式库[/bold] (label 关键字可被 qxt safe reduce 本地抑制)\n")
    idx = 0
    for title, patterns in levels:
        console.print(f"[bold]{title}[/bold]: {len(patterns)} 条")
        for _pattern, label in patterns:
            idx += 1
            if blacklist_override.is_suppressed(label):
                console.print(f"  [dim]{idx:3d}. [已减负] {label}[/dim]")
            else:
                console.print(f"  {idx:3d}. {label}")
        console.print("")

    suppressed = blacklist_override.list_suppressed()
    if suppressed:
        console.print(f"[yellow]当前已抑制关键字 ({len(suppressed)}): {', '.join(suppressed)}[/yellow]")
        console.print("[dim]恢复: qxt safe restore <关键字>[/dim]")
    else:
        console.print("[dim]尚未抑制任何模式。减负示例: qxt safe reduce dd[/dim]")
    return 0


def _cmd_reduce(args) -> int:
    keyword = getattr(args, "keyword", None)
    if not keyword:
        console.print("[red]用法: qxt safe reduce <关键字>[/red]")
        console.print("[dim]关键字会按 label 子串匹配, 例如: dd / format / shutdown / docker[/dim]")
        return 1
    try:
        from ..core import blacklist_override
        if blacklist_override.suppress(keyword):
            console.print(f"[green]已抑制匹配 '{keyword}' 的黑名单模式 (本地生效)[/green]")
            console.print("[dim]查看影响: qxt safe blacklist  |  撤销: qxt safe restore "
                          f"{keyword}[/dim]")
            return 0
        console.print(f"[yellow]关键字 '{keyword}' 已处于抑制状态, 无需重复操作[/yellow]")
        return 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]抑制失败: {exc}[/red]")
        return 1


def _cmd_restore(args) -> int:
    keyword = getattr(args, "keyword", None)
    if not keyword:
        console.print("[red]用法: qxt safe restore <关键字>[/red]")
        return 1
    try:
        from ..core import blacklist_override
        if blacklist_override.release(keyword):
            console.print(f"[green]已恢复匹配 '{keyword}' 的黑名单模式 (重新生效)[/green]")
            return 0
        console.print(f"[yellow]关键字 '{keyword}' 未被抑制, 无需恢复[/yellow]")
        return 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]恢复失败: {exc}[/red]")
        return 1


# ---------------------------------------------------------------- 策略更新

def _cmd_update() -> int:
    console.print("\n[bold]=== 安全策略更新 ===[/bold]\n")
    try:
        from ..ext.security_policy import check_monthly_update, record_monthly_update
        status = check_monthly_update()
        if not status.get("due"):
            console.print(f"[green]✓ {status.get('message', '已是最新')}[/green]")
            console.print("[dim]无需更新[/dim]")
            return 0
        console.print(f"[yellow]{status.get('message', '有待更新项')}[/yellow]")
        record_monthly_update()
        console.print("[green]✓ 已记录更新时间[/green]")
        console.print("\n[bold]更新步骤:[/bold]")
        console.print("  1. 检查新发现的攻击模式")
        console.print("  2. 更新 _CRITICAL_PATTERNS / _HIGH_PATTERNS / _MEDIUM_PATTERNS")
        console.print("  3. 运行测试确认无回归 (pytest tests/)")
        console.print("  4. 更新 CHANGELOG.md 与 SECURITY.md")
        return 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]更新检查失败: {exc}[/red]")
        return 1
