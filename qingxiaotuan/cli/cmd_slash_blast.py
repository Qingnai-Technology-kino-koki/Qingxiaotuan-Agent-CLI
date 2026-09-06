"""斜杠命令 /blast — Blast Radius 交互式可视化。

拆分自 cmd_slash.py。
"""

from __future__ import annotations

from pathlib import Path

from ._ui_singleton import ui


def _cmd_blast(agent, arg: str) -> None:
    """/blast [file|tool|all] — Blast Radius 交互式可视化。

    用法:
      /blast           显示当前会话的影响半径
      /blast <file>    显示特定文件的影响链
      /blast tool      显示工具使用统计
      /blast safety    显示安全风险评估
    """
    from datetime import datetime

    ledger = getattr(getattr(agent, "ctx", None), "ledger", None)
    if ledger is None:
        ui.info("  操作账本未启用。影响半径分析需要 ledger 支持。")
        return

    if ledger.empty():
        ui.info("  操作账本为空: 本次会话尚未修改任何文件。")
        return

    stats = ledger.stats()
    history = ledger.history()
    mode = arg.strip().lower() if arg else "all"

    ui.info("")
    ui.info("  ╔══════════════════════════════════════════════════════════╗")
    ui.info("  ║           🎯 BLAST RADIUS 可视化分析                    ║")
    ui.info("  ╚══════════════════════════════════════════════════════════╝")
    ui.info("")

    if mode == "safety":
        # 安全风险评估
        ui.info("  ┌─ 安全风险评估 ──────────────────────────────────────┐")
        critical_files = []
        config_files = []
        source_files = []
        for f in stats["files"]:
            ext = Path(f).suffix.lower()
            if any(k in f.lower() for k in ("config", "secret", "key", "password", ".env")):
                critical_files.append(f)
            elif ext in (".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"):
                config_files.append(f)
            elif ext in (".py", ".js", ".ts", ".go", ".rs", ".java"):
                source_files.append(f)

        risk_score = 0
        risk_items = []

        if critical_files:
            risk_score += 40
            risk_items.append(("🔴 高风险", f"{len(critical_files)} 个敏感文件", critical_files[:3]))
        if config_files:
            risk_score += 20
            risk_items.append(("🟡 中风险", f"{len(config_files)} 个配置文件", config_files[:3]))
        if source_files:
            risk_score += 10
            risk_items.append(("🟢 低风险", f"{len(source_files)} 个源代码文件", source_files[:3]))

        risk_color = "🔴" if risk_score >= 60 else ("🟡" if risk_score >= 30 else "🟢")
        ui.info(f"  │  整体风险: {risk_color} {risk_score}/100")
        ui.info("  │")

        for level, desc, files in risk_items:
            ui.info(f"  │  {level}: {desc}")
            for f in files:
                ui.info(f"  │    • {f}")

        ui.info("  │")
        ui.info(f"  │  总计: {stats['files_touched']} 个文件, {stats['records']} 次操作")
        ui.info("  └──────────────────────────────────────────────────────┘")
        return

    if mode == "tool":
        # 工具使用统计
        ui.info("  ┌─ 工具使用统计 ──────────────────────────────────────┐")
        tool_counts: dict[str, int] = {}
        for rec in history:
            t = rec["tool"]
            tool_counts[t] = tool_counts.get(t, 0) + 1

        sorted_tools = sorted(tool_counts.items(), key=lambda x: -x[1])
        max_count = sorted_tools[0][1] if sorted_tools else 1

        for tool, count in sorted_tools[:10]:
            bar_len = int(count / max_count * 25)
            bar = "█" * bar_len + "░" * (25 - bar_len)
            ui.info(f"  │  {tool:<20s} {bar} {count:3d}")

        ui.info("  │")
        ui.info(f"  │  总计 {len(tool_counts)} 种工具, {stats['records']} 次调用")
        ui.info("  └──────────────────────────────────────────────────────┘")
        return

    if mode and mode != "all":
        # 特定文件的影响链
        ui.info(f"  ┌─ 文件影响链: {mode} ──────────────────────────────────┐")
        found = False
        for rec in history:
            if mode in rec.get("targets", []):
                found = True
                ts = datetime.fromtimestamp(rec["ts"]).strftime("%H:%M:%S")
                ui.info(f"  │  [{ts}] {rec['tool']}")
                ui.info(f"  │    目标: {', '.join(rec['targets'][:5])}")
        if not found:
            ui.info(f"  │  未找到涉及 {mode} 的操作记录")
        ui.info("  └──────────────────────────────────────────────────────┘")
        return

    # 默认: 完整影响半径展示
    ui.info("  ┌─ 影响半径总览 ──────────────────────────────────────┐")
    ui.info(f"  │  📊 操作总数: {stats['records']}")
    ui.info(f"  │  📁 受影响文件: {stats['files_touched']} 个")
    ui.info("  │")

    # 文件类型分布
    ext_counts: dict[str, int] = {}
    for f in stats["files"]:
        ext = Path(f).suffix or "(无后缀)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    if ext_counts:
        ui.info("  │  文件类型分布:")
        sorted_exts = sorted(ext_counts.items(), key=lambda x: -x[1])
        max_ext_count = sorted_exts[0][1] if sorted_exts else 1
        for ext, count in sorted_exts[:8]:
            bar_len = int(count / max_ext_count * 20)
            bar = "▓" * bar_len + "░" * (20 - bar_len)
            ui.info(f"  │    {ext:<12s} {bar} {count:3d}")

    ui.info("  │")

    # 最近操作时间线
    if history:
        ui.info("  │  最近操作:")
        for rec in history[-8:]:
            ts = datetime.fromtimestamp(rec["ts"]).strftime("%H:%M:%S")
            tool = rec["tool"]
            targets = rec["targets"][:2]
            target_str = ", ".join(targets)
            if len(rec["targets"]) > 2:
                target_str += f" +{len(rec['targets']) - 2}"
            icon = "✏️" if "write" in tool or "edit" in tool else ("🔍" if "read" in tool else "⚡")
            ui.info(f"  │    {ts} {icon} {tool}: {target_str[:40]}")

    ui.info("  │")
    ui.info(f"  │  ── {stats['records']} 次操作 / {stats['files_touched']} 个文件 ──")
    ui.info("  └──────────────────────────────────────────────────────┘")
    ui.info("")
    ui.info("  提示: /blast safety 查看安全风险 / blast tool 查看工具统计")
