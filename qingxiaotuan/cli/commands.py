"""qxt 各子命令实现 + 内部辅助函数 (override / slash / effort 处理)。"""

from __future__ import annotations

import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from .. import __version__
from ..app import build_kernel, create_agent, seed_builtin_skills
from ..config import DEFAULT_CONFIG, Config, dump_yaml, load_dotenv
from ..models import PROVIDER_PRESETS, KNOWN_PROVIDERS, is_known_provider
from ..models.provider_catalog import (
    ALL_PROVIDERS, PROVIDER_CATEGORIES, get_provider, search_providers,
    get_free_providers, get_cn_providers, get_global_providers,
)
from ..logging_conf import log
from ..core.devloop import DevLoop
from ..ui import FullScreenTUI, UI
from ..ui.repl import C

console = Console()
ui = UI(console=console)


# ------------------------------------------------------------------ chat

def _load_session_into_agent(agent, path: Path) -> str:
    """把会话文件的消息重建进 agent.messages (补回系统提示)。返回会话文件名。"""
    from ..memory.sessions import SessionStore
    messages = SessionStore.load_messages(path)
    agent.messages = [{"role": "system", "content": agent._system_prompt}] + messages
    return path.name


def _resume_session(agent, arg: str) -> Optional[str]:
    """从历史会话恢复上下文 (对标 Claude Code --continue/--resume)。

    支持: 数字编号 (最近会话列表 1 起) / 会话文件路径 / 会话 id 前缀 / latest。
    返回会话文件名; 未找到返回 None。
    """
    store = agent.session
    if store is None:
        console.print("[red]会话存储未初始化[/]")
        return None
    candidates = store.list_sessions()
    if arg.isdigit() and 1 <= int(arg) <= len(candidates):
        return _load_session_into_agent(agent, candidates[int(arg) - 1])
    path = Path(arg).expanduser()
    if path.suffix == ".jsonl" and path.exists():
        return _load_session_into_agent(agent, path)
    if arg in ("latest", "last"):
        if candidates:
            return _load_session_into_agent(agent, candidates[0])
    else:
        for p in candidates:
            if p.stem.startswith(arg) or arg in p.name:
                return _load_session_into_agent(agent, p)
    console.print(f"[red]未找到会话: {arg}[/]")
    return None


def _list_sessions(agent) -> None:
    """列出最近会话 (编号 + 标题 + 时间), 供 /resume 选择。"""
    store = agent.session
    if store is None:
        console.print("[red]会话存储未初始化[/]")
        return
    from ..memory.sessions import SessionStore
    sessions = store.list_sessions()[:10]
    if not sessions:
        console.print("[dim]没有历史会话[/]")
        return
    import datetime
    for i, p in enumerate(sessions, 1):
        title = SessionStore.peek_title(p)
        ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
        console.print(f"  {i:2d}. {p.stem}  {ts}  {title}")
    console.print("[dim]输入 /resume <编号|会话id|latest> 恢复[/]")


def _auto_first_run() -> bool:
    """首次运行检测: 若家目录不存在或无 API Key, 自动引导快速设置。

    返回 True 表示已执行设置 (调用方应重新加载配置)。
    """
    home = Path.home() / ".qingxiaotuan"
    if (home / "config.yaml").exists():
        return False
    console.print("[bold cyan]首次使用青小团? 让我们快速设置一下…[/]")
    return cmd_setup(type("Args", (), {"quick": True})())


def cmd_chat(args) -> int:
    """交互式对话 (默认命令)。"""
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1

    config = kernel.require("config")

    # 应用命令行覆盖
    if getattr(args, "model", None):
        config.set_user("model.model", args.model)
    mode = _apply_mode_override(kernel, getattr(args, "mode", None),
                                yes=getattr(args, "yolo", False))
    if getattr(args, "effort", None):
        config.set_user("agent.effort", args.effort)

    workspace = getattr(args, "workspace", None) or os.getcwd()
    effort = config.get("agent.effort", "high")

    agent = create_agent(kernel, workspace)
    seed_builtin_skills(kernel)

    # 会话恢复 (对标 Claude Code --continue/--resume)
    resume_arg = getattr(args, "resume", None)
    if resume_arg:
        loaded = _resume_session(agent, resume_arg)
        if loaded is None:
            return 1
        console.print(f"[green]已恢复会话: {loaded} ({len(agent.messages)} 条消息)[/]")

    # 全屏 TUI 模式
    if getattr(args, "tui", False):
        def submit_in_tui(text: str) -> Optional[str]:
            events: list[str] = []
            answer = agent.run(
                text, stream=False,
                on_tool=lambda name, arguments: (tui.set_mascot("working"), events.append(f"[工具] {name} {arguments[:120]}")),
                on_tool_result=lambda name, result: events.append(f"[结果] {name}: {result[:240]}"),
                on_error=lambda message: events.append(message),
            )
            for event in events:
                tui.append_log(event)
            return answer or "(无输出)"

        def command_in_tui(text: str) -> Optional[str]:
            if text.strip() in ("/exit", "/quit"):
                tui.close()
                return "正在退出…"
            handled = _handle_slash(text, agent, config, workspace)
            tui.set_plan_mode(agent.plan_mode)
            return "命令已执行" if handled else "正在退出…"

        tui = FullScreenTUI(submit_in_tui, on_cancel=agent.cancel, on_command=command_in_tui)
        tui.append_log(f"模型: {config.get('model.provider')}/{config.get('model.model')}")
        tui.append_log(f"工作区: {workspace}")
        tui.run()
        return 0

    # 普通 REPL 模式
    _auto_first_run()
    ui.banner(config, workspace, f"{config.get('model.provider')}/{config.get('model.model')}",
              mode=mode, effort=effort)
    ui.status_bar(mode, effort, workspace)

    while True:
        try:
            user_input = ui.prompt()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见！[/]")
            break
        if not user_input:
            continue
        if user_input.strip() in ("/exit", "/quit"):
            console.print("[dim]再见！[/]")
            break
        if user_input.strip().startswith("/"):
            _handle_slash(user_input.strip(), agent, config, workspace)
            ui.status_bar(mode, effort, workspace, plan=agent.plan_mode)
            continue
        _run_turn(agent, user_input, config)
        ui.status_bar(mode, effort, workspace, plan=agent.plan_mode)

    return 0


def _run_turn(agent, user_input: str, config: Config, stream: bool | None = None):
    """执行一轮对话 (模型 → 工具 → 观察 → 回答)。"""
    _close_reasoning()
    answer = agent.run(
        user_input,
        stream=stream if stream is not None else config.get("model.stream", True),
        on_token=lambda t: ui.stream(t),
        on_tool=lambda n, a: (_close_reasoning(), ui.tool_call(n, _parse_args(a))),
        on_reason=lambda r: _show_reason(r),
        on_tool_result=lambda n, r: ui.tool_result(n, r),
        on_error=lambda m: ui.error(m),
    )
    _close_reasoning()
    if answer:
        if config.get("ui.markdown", True):
            ui.answer_md(answer)
        else:
            ui.info(answer)
    else:
        console.print()
    # 把本次 token 用量与上下文占用回写状态栏
    ui.add_tokens(agent.total_usage.get("prompt_tokens", 0) + agent.total_usage.get("completion_tokens", 0))
    st = agent.context_stats()
    if st.get("budget_tokens"):
        ui.set_context_pct(st["estimated_tokens"] / st["budget_tokens"] * 100)
    if config.get("ui.show_token_usage", True):
        ui.usage(agent.total_usage)
    console.print()


def _close_reasoning():
    pass

def _show_reason(text: str):
    ui.reason(text)

def _parse_args(a):
    try:
        return json.loads(a)
    except Exception:
        return a


# ------------------------------------------------------------------ dev

def cmd_dev(args) -> int:
    """自主开发循环 (分析→实现→自测→核实→汇报)。"""
    task = getattr(args, "task", "")
    if not task:
        console.print("[red]请提供任务描述, 例如: qxt dev \"给项目加一个 CSV 导出功能\"[/]")
        return 1
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    config = kernel.require("config")
    if getattr(args, "model", None):
        config.set_user("model.model", args.model)
    mode = _apply_mode_override(kernel, getattr(args, "mode", None),
                                yes=getattr(args, "yolo", False))
    workspace = getattr(args, "workspace", None) or os.getcwd()
    agent = create_agent(kernel, workspace)
    seed_builtin_skills(kernel)

    def on_checkpoint(result):
        console.print(f"\n[bold cyan]检查点:[/] {result[:200]}")
        try:
            reply = input("  继续 / 调整方向 / done: ").strip()
        except (EOFError, KeyboardInterrupt):
            reply = "done"
        return reply

    loop = DevLoop(agent, config, on_checkpoint=on_checkpoint)
    console.print(f"[bold cyan]开始自主开发:[/] {task}")
    out = loop.run(task, stream=config.get("model.stream", True))
    console.print(f"\n[bold green]完成:[/] {out[:500]}")
    return 0


# ------------------------------------------------------------------ run (headless)

def cmd_run(args) -> int:
    """headless 一次性任务。"""
    task = getattr(args, "task", "")
    if not task:
        console.print("[red]请提供任务描述[/]")
        return 1
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    config = kernel.require("config")
    if getattr(args, "yes", False):
        args.mode = "yolo"
    mode = _apply_mode_override(kernel, getattr(args, "mode", None),
                                yes=getattr(args, "yes", False))
    if getattr(args, "model", None):
        config.set_user("model.model", args.model)
    workspace = getattr(args, "workspace", None) or os.getcwd()
    agent = create_agent(kernel, workspace)
    seed_builtin_skills(kernel)

    if getattr(args, "bg", False):
        from ..core.background import submit_background
        job_id = submit_background(kernel, agent, task, workspace)
        console.print(f"[green]已提交后台任务: {job_id}[/]")
        return 0

    answer = agent.run(
        task,
        stream=not getattr(args, "no_stream", False),
        on_token=lambda t: sys.stdout.write(t) or sys.stdout.flush(),
    )
    if answer:
        console.print(f"\n[bold]{answer}[/]")
    return 0


# ------------------------------------------------------------------ agent (background)

def cmd_agent(args) -> int:
    """后台自主任务。"""
    task = getattr(args, "task", "")
    if not task:
        console.print("[red]请提供任务描述[/]")
        return 1
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    config = kernel.require("config")
    if getattr(args, "yes", False):
        args.mode = "yolo"
    mode = _apply_mode_override(kernel, getattr(args, "mode", None),
                                yes=getattr(args, "yes", False))
    workspace = getattr(args, "workspace", None) or os.getcwd()
    agent = create_agent(kernel, workspace)

    from ..core.background import submit_background
    job_id = submit_background(kernel, agent, task, workspace)
    console.print(f"[green]已提交后台任务: {job_id}[/]")
    if getattr(args, "wait", False):
        from ..core.background import wait_for_job
        console.print("[dim]等待任务完成…[/]")
        result = wait_for_job(kernel, job_id)
        console.print(f"[green]任务完成: {result[:500]}[/]")
    return 0


# ------------------------------------------------------------------ bg

def cmd_bg(args) -> int:
    """后台任务管理。"""
    bg_cmd = getattr(args, "bg_cmd", None)
    if not bg_cmd:
        console.print("[yellow]用法: qxt bg list|logs|cancel|wait[/]")
        return 1
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    from ..core.background_store import list_jobs, get_job, cancel_job
    if bg_cmd == "list":
        jobs = list_jobs()
        if not jobs:
            console.print("[dim]没有后台任务[/]")
        else:
            for j in jobs:
                console.print(f"  {j.get('id','?')[:8]}  {j.get('status','?')}  {j.get('task','')[:60]}")
    elif bg_cmd == "logs":
        job = get_job(getattr(args, "job_id", ""))
        if job:
            console.print(json.dumps(job, indent=2, ensure_ascii=False)[:2000])
        else:
            console.print("[yellow]任务未找到[/]")
    elif bg_cmd == "cancel":
        cancel_job(getattr(args, "job_id", ""))
        console.print("[green]已请求取消[/]")
    elif bg_cmd == "wait":
        from ..core.background import wait_for_job
        result = wait_for_job(kernel, getattr(args, "job_id", ""), timeout=getattr(args, "timeout", 0))
        console.print(f"[green]{result[:500]}[/]")
    return 0


# ------------------------------------------------------------------ bench

def cmd_bench(args) -> int:
    """基准测试。"""
    console.print("[dim]bench 功能开发中…[/]")
    return 0


# ------------------------------------------------------------------ setup

def cmd_setup(args) -> int:
    """初始化向导。"""
    console.print("[bold cyan]青小团初始化向导[/]")
    console.print("  1. 快速设置 (推荐)")
    console.print("  2. 完整设置")
    console.print("  3. 空白设置")
    try:
        choice = input("  选择 [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        choice = "1"

    if choice == "1":
        console.print("\n[bold]快速设置[/]")
        console.print("  可用供应商:")
        for i, name in enumerate(list(PROVIDER_PRESETS.keys())[:12], 1):
            p = PROVIDER_PRESETS[name]
            console.print(f"    {i:2d}. {name:<20s} {p.get('desc','')[:40]}")
        try:
            idx = int(input("  选择编号 [1]: ").strip() or "1") - 1
        except (ValueError, EOFError, KeyboardInterrupt):
            idx = 0
        names = list(PROVIDER_PRESETS.keys())
        provider = names[min(idx, len(names) - 1)]
        preset = PROVIDER_PRESETS[provider]
        console.print(f"\n  已选: {provider}")
        console.print(f"  模型: {preset.get('model', '?')}")
        console.print(f"  网关: {preset.get('base_url', '?')}")
        api_key_env = preset.get("api_key_env", "")
        if api_key_env:
            try:
                key = input(f"  API Key ({api_key_env}, 可选, 回车跳过): ").strip()
            except (EOFError, KeyboardInterrupt):
                key = ""
            if key:
                env_path = Path.home() / ".qingxiaotuan" / ".env"
                env_path.parent.mkdir(parents=True, exist_ok=True)
                with open(env_path, "a", encoding="utf-8") as f:
                    f.write(f"\n{api_key_env}={key}\n")
                console.print(f"  [green]密钥已保存到 {env_path}[/]")

        kernel = build_kernel()
        config = kernel.require("config")
        config.set_user("model.provider", provider)
        config.set_user("model.model", preset.get("model", ""))
        if preset.get("base_url"):
            config.set_user("model.base_url", preset["base_url"])
        if api_key_env:
            config.set_user("model.api_key_env", api_key_env)
        console.print("\n[bold green]设置完成! 现在可以运行 qxt chat 开始对话。[/]")
    else:
        console.print("[dim]完整/空白设置开发中…[/]")
    return 0


# ------------------------------------------------------------------ doctor

def cmd_doctor(args) -> int:
    """环境健康检查。"""
    console.print("[bold cyan]环境检查[/]")
    console.print(f"  Python: {sys.version}")
    console.print(f"  版本: {__version__}")
    kernel = build_kernel()
    config = kernel.require("config")
    provider = config.get("model.provider", "未配置")
    model = config.get("model.model", "未配置")
    console.print(f"  模型: {provider}/{model}")
    key = config.api_key()
    console.print(f"  API Key: {'已配置' if key else '未配置'}")
    # 引擎自检
    try:
        from ..ext.registry import engine_healthcheck
        results = engine_healthcheck()
        ok_count = sum(1 for v in results.values() if v.get("ok"))
        console.print(f"  引擎: {ok_count}/{len(results)} 就绪")
    except Exception:
        console.print("  引擎: 无法检测")
    console.print("[green]检查完成[/]")
    return 0


# ------------------------------------------------------------------ mode

def cmd_mode(args) -> int:
    """查看/切换默认运行模式。"""
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    config = kernel.require("config")
    value = getattr(args, "value", None)
    if value:
        config.set_user("mode.default", value)
        console.print(f"[green]默认模式已切换为: {value}[/]")
    else:
        current = config.get("mode.default", "standard")
        console.print(f"  当前默认模式: {current}")
    return 0


# ------------------------------------------------------------------ model

def cmd_model(args) -> int:
    """配置/热切换模型供应商。"""
    model_cmd = getattr(args, "model_cmd", None)
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    config = kernel.require("config")

    if model_cmd == "current":
        console.print(f"  provider: {config.get('model.provider', '?')}")
        console.print(f"  model:    {config.get('model.model', '?')}")
        console.print(f"  base_url: {config.get('model.base_url', '?')}")
    elif model_cmd == "set":
        # 由子解析器处理
        provider = getattr(args, "provider", None)
        model_name = getattr(args, "model", None)
        base_url = getattr(args, "base_url", None)
        api_key_env = getattr(args, "api_key_env", None)
        if provider:
            config.set_user("model.provider", provider)
        if model_name:
            config.set_user("model.model", model_name)
        if base_url:
            config.set_user("model.base_url", base_url)
        if api_key_env:
            config.set_user("model.api_key_env", api_key_env)
        console.print(f"[green]模型已切换为: {provider or '?'}/{model_name or '?'}[/]")
    elif model_cmd == "switch":
        from ..models.plugin import ModelPlugin
        overrides = {}
        for key in ("provider", "model", "base_url", "api_key_env"):
            val = getattr(args, key, None)
            if val:
                overrides[key] = val
        ModelPlugin.switch_model(kernel, overrides)
        console.print("[green]模型已热切换 (当前会话生效)。[/]")
    elif model_cmd == "info":
        name = getattr(args, "provider_name", "")
        info = get_provider(name)
        if info:
            console.print(f"  {info.name}: {info.desc}")
            console.print(f"  模型: {info.model}")
            console.print(f"  base_url: {info.base_url}")
        else:
            console.print(f"[yellow]未知供应商: {name}[/]")
    elif model_cmd == "list-providers":
        for cat_name, providers in PROVIDER_CATEGORIES.items():
            console.print(f"\n[bold]{cat_name}[/]")
            for p in providers:
                console.print(f"  {p.name:<20s} {p.desc[:50]}")
    else:
        # 无子命令: 交互式选择
        console.print("[bold cyan]选择模型供应商[/]")
        categories = list(PROVIDER_CATEGORIES.keys())
        for i, cat in enumerate(categories, 1):
            console.print(f"  {i}. {cat}")
        try:
            cat_idx = int(input("  分类编号 (回车跳过): ").strip() or "0") - 1
        except (ValueError, EOFError, KeyboardInterrupt):
            cat_idx = -1
        if 0 <= cat_idx < len(categories):
            cat = categories[cat_idx]
            providers = PROVIDER_CATEGORIES[cat]
            for i, p in enumerate(providers, 1):
                console.print(f"    {i}. {p.name:<20s} {p.desc[:40]}")
            try:
                p_idx = int(input("    选择编号 [1]: ").strip() or "1") - 1
            except (ValueError, EOFError, KeyboardInterrupt):
                p_idx = 0
            chosen = providers[min(p_idx, len(providers) - 1)]
            config.set_user("model.provider", chosen.name)
            config.set_user("model.model", chosen.model)
            if chosen.base_url:
                config.set_user("model.base_url", chosen.base_url)
            if chosen.api_key_env:
                config.set_user("model.api_key_env", chosen.api_key_env)
            console.print(f"[green]已选择: {chosen.name}/{chosen.model}[/]")
    return 0


# ------------------------------------------------------------------ config

def cmd_config(args) -> int:
    """配置管理。"""
    config_cmd = getattr(args, "config_cmd", None)
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    config = kernel.require("config")
    if config_cmd == "dump":
        print(dump_yaml(config.data))
    elif config_cmd == "dump-default":
        print(dump_yaml(DEFAULT_CONFIG))
    elif config_cmd == "get":
        key = getattr(args, "key", "")
        console.print(f"  {key} = {config.get(key)}")
    elif config_cmd == "set":
        key = getattr(args, "key", "")
        value = getattr(args, "value", "")
        # 尝试 JSON 解析
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
        config.set_user(key, value)
        console.print(f"[green]{key} = {value}[/]")
    elif config_cmd == "validate":
        console.print("[green]配置校验通过 (TODO: 详细校验)[/]")
    elif config_cmd == "profiles":
        console.print("[dim]可用 profiles: default[/]")
    else:
        console.print("[yellow]用法: qxt config dump|get|set|validate|profiles[/]")
    return 0


# ------------------------------------------------------------------ plugin

def cmd_plugin(args) -> int:
    """插件管理。"""
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    console.print("[bold]插件列表[/]")
    for name, plugin in kernel.plugins.items():
        provides = ", ".join(plugin.provides) if plugin.provides else "-"
        console.print(f"  {name:<30s} provides: {provides}")
    console.print("\n[bold]服务列表[/]")
    for svc in kernel.services:
        console.print(f"  {svc}")
    return 0


# ------------------------------------------------------------------ skill

def cmd_skill(args) -> int:
    """技能管理。"""
    skill_cmd = getattr(args, "skill_cmd", None)
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    manager = kernel.get("skill_manager")
    if not manager:
        console.print("[yellow]技能管理器未初始化[/]")
        return 1
    if skill_cmd == "list":
        skills = manager.list_all()
        if not skills:
            console.print("[dim]没有技能[/]")
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
            console.print(f"[yellow]技能不存在: {name}[/]")
    return 0


# ------------------------------------------------------------------ memory

def cmd_memory(args) -> int:
    """记忆管理。"""
    memory_cmd = getattr(args, "memory_cmd", None)
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"[red]启动失败: {exc}[/]")
        return 1
    store = kernel.get("memory_store")
    if not store:
        console.print("[yellow]记忆存储未初始化[/]")
        return 1
    if memory_cmd == "list":
        console.print(f"  MEMORY.md 条目: {store.count('MEMORY')}")
        console.print(f"  USER.md 条目: {store.count('USER')}")
    elif memory_cmd == "search":
        query = getattr(args, "query", "")
        results = store.search(query) if hasattr(store, "search") else []
        for r in results[:10]:
            console.print(f"  {r}")
    return 0


# ------------------------------------------------------------------ cron

def cmd_cron(args) -> int:
    """定时任务管理。"""
    console.print("[dim]cron 功能开发中…[/]")
    return 0


# ------------------------------------------------------------------ mcp

def cmd_mcp(args) -> int:
    """MCP 协议管理。"""
    console.print("[dim]mcp 功能开发中…[/]")
    return 0


# ------------------------------------------------------------------ session

def cmd_session(args) -> int:
    """会话管理。"""
    session_cmd = getattr(args, "session_cmd", None)
    sessions_dir = Path.home() / ".qingxiaotuan" / "sessions"
    if not sessions_dir.exists():
        console.print("[dim]没有会话记录[/]")
        return 0
    files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if session_cmd == "list":
        for f in files[:10]:
            console.print(f"  {f.stem}  ({f.stat().st_mtime:.0f})")
    else:
        console.print("[dim]session 功能开发中…[/]")
    return 0


# ------------------------------------------------------------------ slash commands

def _handle_slash(cmd: str, agent, config: Config, workspace: str) -> bool:
    """处理斜杠命令。返回 True 表示已处理。"""
    parts = cmd.strip().split(None, 1)
    head = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if head == "/help":
        ui.info(_HELP)
    elif head == "/tools":
        tools = agent.registry.tools
        for t in tools:
            ui.info(f"  {t.name}: {t.description[:60]}")
    elif head == "/skills":
        manager = agent.kernel.get("skill_manager")
        if manager:
            for s in manager.list_all():
                ui.info(f"  {s.name}: {s.description[:60]}")
    elif head == "/memory":
        store = agent.kernel.get("memory_store")
        if store:
            ui.info(f"  记忆条目: {store.count('MEMORY')}")
    elif head == "/usage":
        ui.usage(agent.total_usage)
    elif head == "/cost":
        usage = agent.total_usage
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        hit = usage.get("prompt_cache_hit_tokens", 0)
        miss = usage.get("prompt_cache_miss_tokens", 0)
        ui.info(f"  prompt: {prompt:,} tokens")
        ui.info(f"  completion: {completion:,} tokens")
        if hit or miss:
            ui.info(f"  缓存: 命中 {hit:,} / 未命中 {miss:,}")
            rate = agent.cache_hit_rate()
            if rate is not None:
                ui.info(f"  缓存命中率: {rate:.1%}")
        from ..models.router import estimate_cost
        cost = estimate_cost(config.get("model.provider", ""),
                             config.get("model.model", ""), prompt, completion)
        ui.info(f"  估算成本: ${cost:.4f}")
    elif head == "/compact":
        before = agent.context_stats()["estimated_tokens"]
        dropped = agent.compact()
        after = agent.context_stats()["estimated_tokens"]
        ui.success(f"已压缩: 折叠 {dropped} 条历史, {before:,} → {after:,} tokens")
    elif head == "/context":
        st = agent.context_stats()
        ui.context_bar(st)
        ui.info(f"  预算: {st.get('budget_tokens', '?')} tokens")
        ui.info(f"  已用: {st.get('estimated_tokens', '?')} tokens")
    elif head == "/clear":
        agent.messages.clear()
        ui.info("上下文已清空。")
    elif head == "/more":
        ui.show_more()
    elif head == "/model":
        if arg:
            parts2 = arg.split()
            if parts2 and parts2[0] == "switch":
                from ..models.plugin import ModelPlugin
                ModelPlugin.switch_model(agent.kernel, {
                    k: v for k, v in zip(
                        ["provider", "model", "base_url", "api_key_env"],
                        parts2[1:]
                    )
                })
                ui.success("模型已切换。")
            else:
                ui.info(f"  provider: {config.get('model.provider')}")
                ui.info(f"  model: {config.get('model.model')}")
        else:
            ui.info(f"  provider: {config.get('model.provider')}")
            ui.info(f"  model: {config.get('model.model')}")
    elif head == "/effort":
        if arg:
            config.set_user("agent.effort", arg.strip())
            ui.success(f"推理投入: {arg.strip()}")
        else:
            ui.info(f"  当前: {config.get('agent.effort', 'high')}")
    elif head == "/mode":
        if arg:
            config.set_user("mode.default", arg.strip())
            ui.success(f"模式: {arg.strip()}")
        else:
            ui.info(f"  当前: {config.mode}")
    elif head == "/plan":
        arg_l = arg.strip().lower()
        if arg_l in ("on", "1", "true", "yes"):
            agent.plan_mode = True
            agent.ctx.plan_mode = True
            ui.success("Plan 模式已开启: 只读分析, 修改类工具将被拦截。")
        elif arg_l in ("off", "0", "false", "no"):
            agent.plan_mode = False
            agent.ctx.plan_mode = False
            ui.success("Plan 模式已关闭: 可以正常执行修改操作。")
        else:
            agent.plan_mode = not agent.plan_mode
            agent.ctx.plan_mode = agent.plan_mode
            if agent.plan_mode:
                ui.success("Plan 模式已开启: 只读分析, 修改类工具将被拦截。")
            else:
                ui.success("Plan 模式已关闭: 可以正常执行修改操作。")
    elif head == "/resume":
        if arg:
            name = _resume_session(agent, arg)
            if name:
                ui.success(f"已恢复会话: {name} ({len(agent.messages)} 条消息)")
        else:
            _list_sessions(agent)
    elif head in ("/exit", "/quit"):
        return False
    else:
        ui.info(f"未知命令: {head} (输入 /help 查看可用命令)")
    return True


# ------------------------------------------------------------------ helpers

_HELP = """\n可用斜杠命令 (Slash Commands)
  /help      显示帮助
  /tools     列出可用工具
  /skills    列出技能
  /memory    查看记忆
  /usage     本次会话 token 用量
  /cost      成本估算 (token + 缓存命中率 + 费用)
  /context   上下文占用
  /compact   手动压缩上下文 (折叠旧历史)
  /model     切换/查看模型
  /effort    切换推理投入 low/medium/high
  /mode      切换运行模式 standard/yolo
  /plan      切换 Plan 模式 (只读分析, 修改类工具被拦截)
  /resume    恢复历史会话 (编号/会话id/latest)
  /route     智能模型路由 (根据任务难度选择模型)
  /clear     清空对话上下文
  /more      展开上一条被折叠的长输出
  /exit      退出

提示: Enter 发送 · Esc+Enter 换行 · 多行可用三引号"""


def _apply_mode_override(kernel, mode: Optional[str], yes: bool = False) -> str:
    """应用模式覆盖 (不写用户配置, 避免污染后续启动)。"""
    if yes:
        effective = "yolo"
    elif mode:
        effective = mode
    else:
        effective = kernel.require("config").mode
    if effective != kernel.require("config").mode:
        config = kernel.require("config")
        config.data.setdefault("mode", {})["default"] = effective
    return effective
