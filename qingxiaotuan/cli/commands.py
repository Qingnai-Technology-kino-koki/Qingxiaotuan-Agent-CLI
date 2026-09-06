"""qxt 子命令中枢 —— 惰性 re-export (PEP 562)。

所有子命令已拆分到独立模块。本文件在导入时不加载任何子模块,
而是通过模块级 __getattr__ 在首次访问某命令时才导入对应模块,
使 `qxt skill list` / `qxt config get` 等轻量命令无需付出 app 链 (~1s) 的导入成本。

向后兼容: `from qingxiaotuan.cli.commands import cmd_cron` 与
`qingxiaotuan.cli.commands.cmd_cron(...)` 均照常工作。
"""

from __future__ import annotations

import importlib

_CMD_SOURCES = {
    # ---- cmd_chat (交互 / 模型 / 模式 / 医生) ----
    "cmd_chat": ".cmd_chat",
    "cmd_mode": ".cmd_chat",
    "cmd_model": ".cmd_chat",
    "cmd_doctor": ".cmd_chat",
    "_apply_mode_override": ".cmd_chat",
    "_load_session_into_agent": ".cmd_chat",
    "_resume_session": ".cmd_chat",
    "_list_sessions": ".cmd_chat",
    "_auto_first_run": ".cmd_chat",
    "_run_chat_repl": ".cmd_chat",
    "_run_turn": ".cmd_chat",
    "_show_provider_models": ".cmd_chat",
    "_strip_model_label": ".cmd_chat",
    "_pick_api_key_interactive": ".cmd_chat",
    "_pick_model_interactive": ".cmd_chat",
    "_search_and_pick_model": ".cmd_chat",
    "_find_models": ".cmd_chat",
    "_parse_args": ".cmd_chat",
    "_model_test": ".cmd_chat",
    "_run_session_hooks": ".cmd_chat",
    "_ConfigOverride": ".cmd_chat",
    # ---- cmd_config (轻量配置管理) ----
    "cmd_config": ".cmd_config",
    "_validate_config": ".cmd_config",
    # ---- cmd_network (联网/搜索配置基础设施) ----
    "cmd_network": ".cmd_network",
    # ---- cmd_setup (初始化 / 基准 / 精确引用跳转) ----
    "cmd_setup": ".cmd_setup",
    "cmd_bench": ".cmd_setup",
    "cmd_open": ".cmd_setup",
    "_setup_quick": ".cmd_setup",
    "_setup_full": ".cmd_setup",
    "_setup_blank": ".cmd_setup",
    "_save_api_key": ".cmd_setup",
    "_ask_choice": ".cmd_setup",
    "_bench_cache": ".cmd_setup",
    "_bench_latency": ".cmd_setup",
    # ---- cmd_services (Plugin / Skill / Memory, 不含已拆分子命令) ----
    "cmd_plugin": ".cmd_services",
    "cmd_skill": ".cmd_services",
    "cmd_memory": ".cmd_services",
    # ---- cmd_cron (拆分自 cmd_services) ----
    "cmd_cron": ".cmd_cron",
    "_cron_pid_file": ".cmd_cron",
    "_cron_daemon_alive": ".cmd_cron",
    "_cron_spawn_daemon": ".cmd_cron",
    # ---- cmd_mcp_cli (拆分自 cmd_services) ----
    "cmd_mcp": ".cmd_mcp_cli",
    "_mcp_build_kernel": ".cmd_mcp_cli",
    "_mcp_list": ".cmd_mcp_cli",
    "_mcp_tools": ".cmd_mcp_cli",
    "_mcp_call": ".cmd_mcp_cli",
    "_mcp_add": ".cmd_mcp_cli",
    "_mcp_test": ".cmd_mcp_cli",
    "_mcp_audit": ".cmd_mcp_cli",
    "_mcp_security": ".cmd_mcp_cli",
    # ---- cmd_hooks (拆分自 cmd_services) ----
    "cmd_hooks": ".cmd_hooks",
    "_hooks_list": ".cmd_hooks",
    "_hooks_test": ".cmd_hooks",
    "_cmd_hooks": ".cmd_hooks",
    # ---- cmd_session (拆分自 cmd_services) ----
    "cmd_session": ".cmd_session",
    "_format_session_time": ".cmd_session",
    "_count_session_messages": ".cmd_session",
    "_resolve_session_target": ".cmd_session",
    # ---- cmd_usercmd (拆分自 cmd_services) ----
    "cmd_usercmd": ".cmd_usercmd",
    # ---- cmd_slash (REPL 斜杠命令) ----
    "_handle_slash": ".cmd_slash",
    "_cmd_image": ".cmd_slash",
    "_cmd_permissions": ".cmd_slash",
    "_cmd_status": ".cmd_slash",
    "_cmd_stats": ".cmd_slash",
    "_cmd_budget": ".cmd_slash",
    "_cmd_checkpoint": ".cmd_slash",
    "_cmd_web": ".cmd_slash",
    "_cmd_subagent": ".cmd_slash",
    "_cmd_verify": ".cmd_slash",
    "_cmd_log": ".cmd_slash",
    "_detect_project_type": ".cmd_slash",
    "_HELP": ".cmd_slash",
    # ---- cmd_agents (Dev / Run / Agent / Bg / Undo / Impact) ----
    "cmd_dev": ".cmd_agents",
    "cmd_run": ".cmd_agents",
    "cmd_agent": ".cmd_agents",
    "cmd_bg": ".cmd_agents",
    "cmd_undo": ".cmd_agents",
    "cmd_impact": ".cmd_agents",
    "_cmd_diff": ".cmd_agents",
    "_cmd_undo": ".cmd_agents",
    "_cmd_impact": ".cmd_agents",
    "_get_ledger": ".cmd_agents",
    "_git_run": ".cmd_agents",
    # ---- cmd_agents_view ----
    "cmd_agents": ".cmd_agents_view",
    "cmd_agents_view": ".cmd_agents_view",
    # ---- cmd_safe (安全总入口: 白名单 / 本地黑名单减负 / 状态 / 更新) ----
    "cmd_safe": ".cmd_safe",
    # ---- cmd_others (能力目录 / 新手入口) ----
    "cmd_others": ".cmd_others",
    # ---- cmd_code_edit (代码编辑助手) ----
    "cmd_code_edit": ".cmd_code_edit",
    "cmd_harden": ".cmd_harden",
    # ---- cmd_tutorial (任务驱动内置教程) ----
    "cmd_tutorial": ".cmd_tutorial",
}

__all__ = sorted(_CMD_SOURCES) + ["ui"]


def __getattr__(name: str):
    if name == "ui":
        from ._ui_singleton import ui as _ui
        return _ui
    mod_name = _CMD_SOURCES.get(name)
    if mod_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod = importlib.import_module(mod_name, __package__)
    return getattr(mod, name)
