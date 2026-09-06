"""斜杠命令 /mcp 与 /audit — MCP 管理与审计报告。

拆分自 cmd_slash.py。
"""

from __future__ import annotations

from ._ui_singleton import ui


def _cmd_mcp(agent, arg: str) -> None:
    """/mcp [list|tools|audit|security] [args] — MCP 管理。

    用法:
      /mcp                 列出已连接的 MCP server
      /mcp tools [server]  列出 MCP 工具
      /mcp audit [N]       查看 MCP 调用审计日志
      /mcp security        查看安全策略
    """
    from datetime import datetime

    parts = arg.split(None, 1) if arg else []
    action = parts[0].lower() if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    # 获取 MCP 客户端列表
    clients = agent.kernel.get("mcp_clients") or []

    if action == "list" or not action:
        ui.info("  [MCP] 已连接的服务器:")
        if not clients:
            ui.info("    没有已连接的 MCP server")
            ui.info("    配置方法: qxt config set mcp.servers '[{...}]'")
        else:
            for client in clients:
                tools = client.list_tools()
                ui.info(f"    • {client.name}: {len(tools)} 个工具")
                ui.info(f"      命令: {client.command} {' '.join(client.args)}")
                ui.info(f"      超时: {client.timeout}s")
        return

    if action == "tools":
        ui.info("  [MCP] 工具列表:")
        if not clients:
            ui.info("    没有已连接的 MCP server")
            return

        target = rest.strip() if rest else None
        for client in clients:
            if target and client.name != target:
                continue
            tools = client.list_tools()
            if not tools:
                continue
            ui.info(f"    {client.name} ({len(tools)} 个工具):")
            for tool in tools:
                name = tool.get("name", "?")
                desc = tool.get("description", "")[:60]
                ui.info(f"      • {name}: {desc}")
        return

    if action == "audit":
        n = 20
        if rest:
            try:
                n = int(rest)
            except ValueError:
                pass

        ui.info(f"  [MCP] 最近 {n} 条调用审计:")
        if not clients:
            ui.info("    没有已连接的 MCP server")
            return

        all_logs = []
        for client in clients:
            logs = client.get_audit_log(n)
            for log_entry in logs:
                log_entry["server"] = client.name
                all_logs.append(log_entry)

        # 按时间排序
        all_logs.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        all_logs = all_logs[:n]

        if not all_logs:
            ui.info("    暂无调用记录")
        else:
            for entry in all_logs:
                ts = datetime.fromtimestamp(entry.get("timestamp", 0)).strftime("%H:%M:%S")
                tool = entry.get("tool", "?")
                server = entry.get("server", "?")
                success = "✓" if entry.get("success") else "✗"
                ui.info(f"    [{ts}] {success} {server}/{tool}")
        return

    if action == "security":
        ui.info("  [MCP] 安全策略:")
        if not clients:
            ui.info("    没有已连接的 MCP server")
            return

        for client in clients:
            policy = client.security_policy
            ui.info(f"    {client.name}:")
            ui.info(f"      频率限制: {policy.max_calls_per_minute}/分钟")
            ui.info(f"      审计日志: {'启用' if policy.audit_enabled else '禁用'}")
            if policy.allowed_tools:
                ui.info(f"      工具白名单: {', '.join(policy.allowed_tools)}")
            if policy.denied_tools:
                ui.info(f"      工具黑名单: {', '.join(policy.denied_tools)}")
            if policy.require_confirm_tools:
                ui.info(f"      需确认工具: {', '.join(policy.require_confirm_tools)}")
        return

    ui.error(f"未知操作: {action}. 可用: list / tools / audit / security")


def _cmd_audit(agent, arg: str) -> None:
    """/audit [export|stats|recent] [args] — 审计报告管理。

    用法:
      /audit                显示审计统计
      /audit stats          显示详细统计
      /audit recent [N]     显示最近 N 条审计事件
      /audit export <fmt> <path>  导出审计报告
    """
    parts = arg.split(None, 2) if arg else []
    action = parts[0].lower() if parts else "stats"

    # 获取审计存储
    audit_store = agent.kernel.get("audit_store")
    if audit_store is None:
        ui.info("  审计存储未启用。配置中设置 audit.enabled=true。")
        return

    if action == "stats" or not action:
        ui.info("  [审计] 统计信息:")
        try:
            stats = audit_store.stats()
            ui.info(f"    状态: {'已启用' if stats['enabled'] else '未启用'}")
            ui.info(f"    持久化: {'是' if stats['persist'] else '否'}")
            ui.info(f"    内存缓冲: {stats['buffered']} 条")
            ui.info(f"    总事件数: {stats['total']} 条")
            if stats['by_type']:
                ui.info("    事件类型分布:")
                for t, c in sorted(stats['by_type'].items(), key=lambda x: -x[1])[:10]:
                    ui.info(f"      {t}: {c}")
            if stats['oldest_iso']:
                ui.info(f"    最早事件: {stats['oldest_iso']}")
            if stats['newest_iso']:
                ui.info(f"    最新事件: {stats['newest_iso']}")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"获取审计统计失败: {exc}")
        return

    if action == "recent":
        n = 20
        if len(parts) > 1:
            try:
                n = int(parts[1])
            except ValueError:
                pass
        ui.info(f"  [审计] 最近 {n} 条事件:")
        try:
            from datetime import datetime
            events = audit_store.recent(n)
            if not events:
                ui.info("    暂无审计事件")
            else:
                for ev in events:
                    ts = datetime.fromtimestamp(ev.ts).strftime("%H:%M:%S")
                    ui.info(f"    [{ev.seq}] {ts} {ev.type}")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"获取审计事件失败: {exc}")
        return

    if action == "export":
        if len(parts) < 3:
            ui.error("用法: /audit export <format> <path>")
            ui.info("格式: json / csv / markdown / html / soc2")
            ui.info("示例: /audit export markdown ./audit-report.md")
            return

        fmt = parts[1].lower()
        output_path = parts[2]

        if fmt not in ("json", "csv", "markdown", "html", "soc2"):
            ui.error(f"不支持的格式: {fmt}")
            ui.info("可选格式: json / csv / markdown / html / soc2")
            return

        ui.info(f"  [审计] 导出报告: {fmt} -> {output_path}")
        try:
            from ..audit.report import generate_report
            result = generate_report(
                audit_store,
                output_path,
                fmt=fmt,
                title=f"青小团审计报告 - {agent.config.get('model.provider', 'unknown')}",
            )
            ui.success(f"报告已导出: {result['path']}")
            ui.info(f"  包含 {result['count']} 条事件")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"导出失败: {exc}")
        return

    ui.error(f"未知操作: {action}. 可用: stats / recent / export")
