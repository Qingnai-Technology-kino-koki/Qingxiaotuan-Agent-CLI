"""`qxt mcp` —— MCP 协议管理 (拆分自 cmd_services.py)。"""

from __future__ import annotations

import datetime
import json

from ..config import Config
from ._ui_singleton import console
from ..ui.format import Table


def build_kernel(*a, **k):
    """惰性构建内核: 仅实际执行 mcp 命令时才加载 app 链。"""
    from ..app import build_kernel as _f
    return _f(*a, **k)


def _mcp_build_kernel():
    """构建内核并取出已连接的 MCP 客户端列表。"""
    try:
        kernel = build_kernel()
    except Exception as exc:  # noqa: BLE001
        console.print(f"内核启动失败: {exc}")
        return None, []
    try:
        clients = kernel.require("mcp_clients")
    except Exception:
        clients = []
    return kernel, clients


def _mcp_list(args) -> int:
    # 轻量路径: 未配置任何 server 时无需构建内核 (常见情况)
    cfg = Config(profile=getattr(args, "profile", "default"),
                 patch_file=getattr(args, "patch", None))
    if not (cfg.get("mcp.servers", []) or []):
        console.print("未配置任何 MCP server。用 `qxt mcp add <name> <command>` 接入外部工具。")
        return 0
    kernel, clients = _mcp_build_kernel()
    if kernel is None:
        return 1
    cfg = kernel.require("config")
    servers = cfg.get("mcp.servers", []) or []
    table = Table(title="MCP Servers")
    table.add_column("Server")
    table.add_column("Command")
    table.add_column("Status")
    table.add_column("Tools")
    client_by_name = {c.name: c for c in clients}
    for s in servers:
        name = s.get("name", s.get("command", "?"))
        command = " ".join([s.get("command", "")] + list(s.get("args", []) or []))
        client = client_by_name.get(name)
        if client is None:
            table.add_row(name, command, "未连接", "-")
        else:
            try:
                n = len(client.list_tools())
                table.add_row(name, command, "已连接", str(n))
            except Exception as exc:  # noqa: BLE001
                table.add_row(name, command, f"错误: {exc}", "-")
    console.print(table)
    return 0


def _mcp_tools(args) -> int:
    kernel, clients = _mcp_build_kernel()
    if kernel is None:
        return 1
    target = getattr(args, "server", None)
    if not clients:
        console.print("没有已连接的 MCP server。")
        return 0
    shown = 0
    for client in clients:
        if target and client.name != target:
            continue
        try:
            specs = client.list_tools()
        except Exception as exc:  # noqa: BLE001
            console.print(f"{client.name} 列举工具失败: {exc}")
            continue
        console.print(f"{client.name} — {len(specs)} 个工具")
        for spec in specs:
            desc = (spec.get("description") or "")[:90]
            console.print(f"  • {spec.get('name')}: {desc}")
        shown += 1
    if target and shown == 0:
        console.print(f"未找到已连接的 MCP server: {target}")
        return 1
    return 0


def _mcp_call(args) -> int:
    kernel, clients = _mcp_build_kernel()
    if kernel is None:
        return 1
    server = getattr(args, "server")
    tool = getattr(args, "tool")
    raw = getattr(args, "json", None) or "{}"
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"参数不是合法 JSON: {exc}")
        return 1
    client = next((c for c in clients if c.name == server), None)
    if client is None:
        console.print(f"未找到已连接的 MCP server: {server}")
        return 1
    try:
        result = client.call_tool(tool, arguments)
    except Exception as exc:  # noqa: BLE001
        console.print(f"调用失败: {exc}")
        return 1
    console.print(result)
    return 0


def _mcp_add(args) -> int:
    name = getattr(args, "name")
    command = getattr(args, "command")
    extra_args = list(getattr(args, "args", None) or [])
    env_list = list(getattr(args, "env", None) or [])
    timeout = getattr(args, "timeout", None)
    env = {}
    for kv in env_list:
        if "=" in kv:
            k, v = kv.split("=", 1)
            env[k] = v
        else:
            console.print(f"忽略无效的环境变量 (应为 KEY=VALUE): {kv}")
    server = {"name": name, "command": command, "args": extra_args, "env": env}
    if timeout:
        server["timeout"] = timeout
    try:
        config = Config()
    except Exception as exc:  # noqa: BLE001
        console.print(f"读取配置失败: {exc}")
        return 1
    servers = list(config.get("mcp.servers", []) or [])
    if any(s.get("name") == name for s in servers):
        console.print(f"已存在同名 server '{name}', 覆盖其配置。")
        servers = [s for s in servers if s.get("name") != name]
    servers.append(server)
    config.set_user("mcp.servers", servers)
    console.print(f"已添加 MCP server {name} -> {command} {' '.join(extra_args)}")
    console.print("下次启动会话 (或运行 `qxt mcp list`) 时即会加载该 server。")
    return 0


def _mcp_test(args) -> int:
    kernel, clients = _mcp_build_kernel()
    if kernel is None:
        return 1
    name = getattr(args, "name")
    client = next((c for c in clients if c.name == name), None)
    if client is None:
        console.print(f"未找到已连接的 MCP server: {name}")
        return 1
    try:
        specs = client.list_tools()
    except Exception as exc:  # noqa: BLE001
        console.print(f"{name} 连通性测试失败: {exc}")
        return 1
    console.print(f"{name} 连接正常, 共 {len(specs)} 个工具可用:")
    for spec in specs:
        console.print(f"  • {spec.get('name')}")
    return 0


def _mcp_audit(args) -> int:
    """查看 MCP 调用审计日志 (持久化 JSONL)。"""
    from ..tools.mcp.audit import MCPAuditStore
    from ..tools.mcp.client import _get_global_audit_store

    if getattr(args, "clear", False):
        store = MCPAuditStore()
        store.clear()
        console.print(f"已清空审计日志: {store.path}")
        return 0

    limit = getattr(args, "limit", 20) or 20
    store = _get_global_audit_store()
    entries = store.read(limit)
    if not entries:
        console.print(f"审计日志为空 (路径: {store.path})。启用 mcp.security.audit_enabled 后每次工具调用都会记录。")
        return 0

    table = Table(title=f"MCP 调用审计 (最近 {len(entries)} 条)")
    table.add_column("时间")
    table.add_column("Server")
    table.add_column("工具")
    table.add_column("结果")
    table.add_column("参数")
    for e in entries:
        ts = e.get("timestamp", 0)
        try:
            tstr = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            tstr = "-"
        args_summary = json.dumps(e.get("arguments", {}), ensure_ascii=False)
        if len(args_summary) > 60:
            args_summary = args_summary[:60] + "…"
        status = "✓" if e.get("success") else "✗"
        table.add_row(
            tstr,
            str(e.get("server", "-")),
            str(e.get("tool", "-")),
            status,
            args_summary,
        )
    console.print(table)
    console.print(f"\n审计日志路径: {store.path}")
    return 0


def _mcp_security(args) -> int:
    """显示各 MCP server 的安全策略。"""
    from ..tools.mcp.plugin import _build_security_policy

    kernel, clients = _mcp_build_kernel()
    if kernel is None:
        return 1
    cfg = kernel.require("config")
    servers = cfg.get("mcp.servers", []) or []
    if not servers:
        console.print("未配置任何 MCP server。用 `qxt mcp add <name> <command>` 接入外部工具。")
        return 0
    target = getattr(args, "server", None)
    table = Table(title="MCP 安全策略")
    table.add_column("Server")
    table.add_column("白名单")
    table.add_column("黑名单")
    table.add_column("需确认")
    table.add_column("频率/分钟")
    table.add_column("审计")
    table.add_column("沙箱")
    for s in servers:
        name = s.get("name", s.get("command", "?"))
        if target and name != target:
            continue
        policy = _build_security_policy(s, cfg)
        allowed = ",".join(sorted(policy.allowed_tools)) if policy.allowed_tools is not None else "*"
        denied = ",".join(sorted(policy.denied_tools)) if policy.denied_tools else "-"
        confirm = ",".join(sorted(policy.require_confirm_tools)) if policy.require_confirm_tools else "-"
        table.add_row(
            name,
            allowed[:40],
            denied[:40],
            confirm[:40],
            str(policy.max_calls_per_minute),
            "开" if policy.audit_enabled else "关",
            "开" if policy.sandbox_enabled else "关",
        )
    console.print(table)
    console.print("\n配置位置: 各 server 的 security: 段 (allowed_tools / denied_tools / require_confirm_tools / max_calls_per_minute / audit_enabled / sandbox)。")
    return 0


def cmd_mcp(args) -> int:
    """MCP 协议管理。"""
    sub = getattr(args, "mcp_cmd", None)
    if sub == "list":
        return _mcp_list(args)
    if sub == "tools":
        return _mcp_tools(args)
    if sub == "call":
        return _mcp_call(args)
    if sub == "add":
        return _mcp_add(args)
    if sub == "test":
        return _mcp_test(args)
    if sub == "audit":
        return _mcp_audit(args)
    if sub == "security":
        return _mcp_security(args)
    console.print("未知子命令。可用: list / tools / call / add / test / audit / security")
    return 1
