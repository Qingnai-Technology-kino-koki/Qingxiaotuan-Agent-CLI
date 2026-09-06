"""服务管理 CLI 命令: MCP / Cron / Hooks / Memory / Skill / Plugin / Session / Usercmd。

拆分自 commands.py。本文件保留 plugin / skill / memory 与所有旧导入路径,
MCP / Cron / Hooks / Session / Usercmd 已拆至 cmd_mcp_cli / cmd_cron /
cmd_hooks / cmd_session / cmd_usercmd。
"""

from __future__ import annotations

import importlib

from ..config import Config
from ..memory import MemoryStore
from ..skills import SkillManager
from ._ui_singleton import console


def build_kernel(*a, **k):
    """惰性构建内核: 仅 plugin 命令等真正需要完整内核时才加载 app 链。"""
    from ..app import build_kernel as _f
    return _f(*a, **k)

# ---- 拆分出去的子命令: 惰性 re-export (PEP 562)。
# 避免访问任一子命令时连带加载 cron/mcp/hooks/session/usercmd 全家桶
# (cron 包会拖入 core/tools 全链, ~400ms)。 ----
_SUBMODULES = {
    "cmd_cron": (".cmd_cron", "cmd_cron"),
    "_cron_pid_file": (".cmd_cron", "_cron_pid_file"),
    "_cron_daemon_alive": (".cmd_cron", "_cron_daemon_alive"),
    "_cron_spawn_daemon": (".cmd_cron", "_cron_spawn_daemon"),
    "cmd_hooks": (".cmd_hooks", "cmd_hooks"),
    "_hooks_list": (".cmd_hooks", "_hooks_list"),
    "_hooks_test": (".cmd_hooks", "_hooks_test"),
    "_cmd_hooks": (".cmd_hooks", "_cmd_hooks"),
    "cmd_mcp": (".cmd_mcp_cli", "cmd_mcp"),
    "_mcp_build_kernel": (".cmd_mcp_cli", "_mcp_build_kernel"),
    "_mcp_list": (".cmd_mcp_cli", "_mcp_list"),
    "_mcp_tools": (".cmd_mcp_cli", "_mcp_tools"),
    "_mcp_call": (".cmd_mcp_cli", "_mcp_call"),
    "_mcp_add": (".cmd_mcp_cli", "_mcp_add"),
    "_mcp_test": (".cmd_mcp_cli", "_mcp_test"),
    "_mcp_audit": (".cmd_mcp_cli", "_mcp_audit"),
    "_mcp_security": (".cmd_mcp_cli", "_mcp_security"),
    "cmd_session": (".cmd_session", "cmd_session"),
    "_format_session_time": (".cmd_session", "_format_session_time"),
    "_count_session_messages": (".cmd_session", "_count_session_messages"),
    "_resolve_session_target": (".cmd_session", "_resolve_session_target"),
    "cmd_usercmd": (".cmd_usercmd", "cmd_usercmd"),
}


def __getattr__(name: str):
    mapping = _SUBMODULES.get(name)
    if mapping is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod = importlib.import_module(mapping[0], __package__)
    return getattr(mod, mapping[1])


# ===================================================================== cmd_plugin

def cmd_plugin(args) -> int:
    """插件管理。"""
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    console.print("插件列表")
    for name, plugin in kernel.plugins.items():
        provides = ", ".join(plugin.provides) if plugin.provides else "-"
        console.print(f"  {name:<30s} provides: {provides}")
    console.print("\n服务列表")
    for svc in kernel.services.services:
        console.print(f"  {svc}")
    return 0


# ===================================================================== cmd_skill

def cmd_skill(args) -> int:
    """技能管理。"""
    skill_cmd = getattr(args, "skill_cmd", None)
    config = Config(profile=getattr(args, "profile", "default"),
                    patch_file=getattr(args, "patch", None))
    manager = SkillManager(config.home)
    if skill_cmd == "list":
        skills = manager.list_all()
        if not skills:
            console.print("没有技能")
        else:
            for s in skills:
                console.print(f"  {s.name:<30s} {s.description[:50]}")
    elif skill_cmd == "show":
        name = getattr(args, "name", "")
        skill = manager.load(name)
        if skill:
            console.print(f"  名称: {skill.name}")
            console.print(f"  描述: {skill.description}")
            console.print(f"\n{skill.body}")
        else:
            console.print(f"技能不存在: {name}")
    return 0


# ===================================================================== cmd_memory

def cmd_memory(args) -> int:
    """记忆管理。"""
    memory_cmd = getattr(args, "memory_cmd", None)
    config = Config(profile=getattr(args, "profile", "default"),
                    patch_file=getattr(args, "patch", None))
    store = MemoryStore(config.home)
    if memory_cmd == "list":
        stats = store.get_stats() if hasattr(store, "get_stats") else {}
        console.print(f"  MEMORY.md 条目: {stats.get('memory_lines', 0)}")
        console.print(f"  USER.md 条目: {stats.get('user_lines', 0)}")
        console.print(f"  全文索引条目: {stats.get('fts_entries', 0)}")
        console.print(f"  标签条目: {stats.get('tag_entries', 0)}")
    elif memory_cmd == "search":
        query = getattr(args, "query", "")
        results = store.search(query) if hasattr(store, "search") else []
        for r in results[:10]:
            console.print(f"  {r}")
    return 0
