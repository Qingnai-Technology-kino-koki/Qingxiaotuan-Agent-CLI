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

def _auto_first_run() -> bool:
    """首次运行检测: 若家目录不存在或无 API Key, 自动引导快速设置。

    返回 True 表示已执行设置 (调用方应重新加载配置)。
    """
    from ..config.loader import Config as _Cfg
    cfg = _Cfg()
    # 家目录不存在, 或存在但无 API Key 且无 config.yaml
    if not cfg.home.exists():
        console.print("\n[cyan]👋 欢迎使用青小团 CLI![/]")
        console.print("[dim]检测到首次运行, 自动进入快速设置...[/]")
        console.print("[dim] (跳过: qxt setup --quick 或 qxt model set ...)[/]")
        console.print()
        return _setup_quick(cfg)
    if not cfg.api_key() and not (cfg.home / "config.yaml").exists():
        console.print("\n[cyan]👋 检测到未配置 API Key[/]")
        console.print("[dim]快速设置只需 30 秒: 选供应商 → 填 Key → 完成[/]")
        try:
            choice = input("是否现在设置? [Y/n]: ").strip().lower()
        except EOFError:
            return False
        if choice in ("", "y", "yes"):
            return _setup_quick(cfg)
    return False


def cmd_chat(args) -> int:
    # 首次运行自动引导
    if _auto_first_run():
        # 设置完成后重新加载配置
        pass
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    config: Config = kernel.require("config")
    workspace = str(Path(args.workspace or os.getcwd()).resolve())
    ui.home = config.home
    mode = _apply_mode_override(kernel, getattr(args, "mode", None))
    _apply_model_override(kernel, getattr(args, "model", None))
    effort = _apply_effort_override(kernel, getattr(args, "effort", None))
    ui.banner(config, workspace, f"{config.get('model.provider')}/{config.get('model.model')}",
              args.profile, mode, effort)
    agent = create_agent(kernel, workspace, confirm=_confirm_cli)
    if mode == "yolo":
        agent.ctx.on_auto_approve = lambda name: ui.info(f"⚡ 自动批准 · {name} (已写入审计日志)")

    if getattr(args, "resume", None):
        from ..memory import SessionStore
        msgs = SessionStore.load_messages(Path(args.resume))
        if msgs:
            agent.messages.extend(msgs)
            ui.info(f"已恢复会话: {args.resume} ({len(msgs)} 条消息)")

    if getattr(args, "tui", False):
        def submit_in_tui(text: str) -> str:
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
            return "命令已执行" if handled else "正在退出…"

        tui = FullScreenTUI(submit_in_tui, on_cancel=agent.cancel, on_command=command_in_tui)
        tui.append_log(f"模型: {config.get('model.provider')}/{config.get('model.model')}")
        tui.append_log(f"工作区: {workspace}")
        tui.run()
        _auto_memory_on_exit(agent, config)
        return 0

    while True:
        ui.status_bar(mode, effort, workspace)
        try:
            user_input = ui.prompt()
        except (EOFError, KeyboardInterrupt):
            ui.info("再见。")
            break
        if not user_input:
            continue
        if user_input.startswith("/"):
            # /effort 可能在 _handle_slash 内改 effort, 需要回写
            if user_input.startswith("/effort"):
                effort = _handle_effort(user_input, config, agent) or effort
                continue
            if not _handle_slash(user_input, agent, config, workspace):
                break
            continue
        try:
            _run_turn(agent, user_input, config)
        except KeyboardInterrupt:
            ui.info("已中断当前回合。")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"{type(exc).__name__}: {exc}")

    # 自动固化: 会话结束后把本次主题写入长期记忆 (跨会话连续性)
    _auto_memory_on_exit(agent, config)
    return 0


def _auto_memory_on_exit(agent, config: Config) -> None:
    """若开启 agent.auto_memory 且发生过对话, 把本次会话主题记录进长期记忆。"""
    if not config.get("agent.auto_memory", True):
        return
    if agent.turn_count == 0:
        return
    # 取首条 user 消息作为主题预览
    topic = ""
    for m in agent.messages:
        if m.get("role") == "user" and m.get("content"):
            topic = m["content"][:80].replace("\n", " ")
            break
    if not topic:
        return
    try:
        store = agent.kernel.get("memory_store")
        if store is None:
            return
        line = f"会话主题: {topic} (共 {agent.turn_count} 轮)"
        store.append_memory(line, section="会话")
    except Exception:  # noqa: BLE001
        pass


def _run_turn(agent, user_input: str, config: Config, stream: bool | None = None) -> None:
    stream = config.get("model.stream", True) if stream is None else stream
    # 把 UI 句柄注入工具上下文, 让安全拦截时能切换吉祥物状态
    if getattr(agent, "ctx", None) is not None:
        agent.ctx.ui = ui
    ui.think_start()
    first = {"t": True}

    def on_token(tok: str) -> None:
        if first["t"]:
            console.print()  # 思考行 -> 正式输出之间换行
            first["t"] = False
        ui.stream(tok)

    answer = agent.run(
        user_input, stream=stream,
        on_token=on_token,
        on_tool=lambda n, a: ui.tool_call(n, a),
        on_reason=lambda r: ui.reason(r),
        on_tool_result=lambda n, res: ui.tool_result(n, res),
        on_error=lambda msg: ui.error(msg),
    )
    # 任务结束: 吉祥物进入完成态
    ui.mascot_set("done")
    if not stream:
        ui.answer_md(answer or "(无输出)")
    else:
        console.print()
    # 把本次 token 用量与上下文占用回写状态栏, 让 tok= / ctx= 常驻可见
    ui.add_tokens(agent.total_usage.get("prompt_tokens", 0) + agent.total_usage.get("completion_tokens", 0))
    st = agent.context_stats()
    if st.get("budget_tokens"):
        ui.set_context_pct(st["estimated_tokens"] / st["budget_tokens"] * 100)
    if config.get("ui.show_token_usage", True):
        ui.usage(agent.total_usage)
    # 回合结束重绘状态栏, 反映最新的 tok= / ctx= / 吉祥物状态
    ui.status_bar(mode, effort, workspace)
    console.print()


# ------------------------------------------------------------------ dev (自主循环)

def cmd_dev(args) -> int:
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    config: Config = kernel.require("config")
    workspace = str(Path(args.workspace or os.getcwd()).resolve())
    ui.home = config.home
    if not config.get("loop.enabled", True):
        ui.error("loop 已在配置中关闭 (loop.enabled=false)。")
        return 1
    mode = _apply_mode_override(kernel, getattr(args, "mode", None))
    _apply_model_override(kernel, getattr(args, "model", None))
    effort = _apply_effort_override(kernel, getattr(args, "effort", None))
    agent = create_agent(kernel, workspace, confirm=_confirm_cli)
    if mode == "yolo":
        agent.ctx.on_auto_approve = lambda name: ui.info(f"⚡ 自动批准 · {name} (已写入审计日志)")
    task = args.task

    def on_iteration(n: int, total: int, note: str) -> None:
        if n < 0:
            ui.phase(note)
        else:
            ui.phase(f"自主开发循环 · 第 {n}/{total} 轮")

    def on_checkpoint(report: str) -> str:
        return ui.ask(
            f"第 {agent.turn_count} 轮已完成。下面是本轮汇报:\n\n{report[:1500]}\n\n"
            "是否满意并结束? 还是给反馈继续?",
            options="[继续 C] 继续下一轮 · [完成 D] 满意, 结束 · 或直接输入反馈让 Agent 调整",
        )

    loop = DevLoop(agent, config, on_iteration=on_iteration, on_checkpoint=on_checkpoint)
    ui.banner(config, workspace, f"{config.get('model.provider')}/{config.get('model.model')}",
              args.profile, mode, effort)
    ui.report("开发循环已启动", f"任务: {task}\n\n自主迭代: 分析→规划→实现→自测→汇报, 每轮确认进度, 满意即停。")
    try:
        final = loop.run(
            task, stream=config.get("model.stream", True),
            on_token=lambda t: ui.stream(t),
            on_tool=lambda n, a: ui.tool_call(n, a),
            on_reason=lambda r: ui.reason(r),
            on_tool_result=lambda n, res: ui.tool_result(n, res),
            on_error=lambda msg: ui.error(msg),
        )
    except KeyboardInterrupt:
        ui.info("已中断循环。")
        return 0
    console.print()
    ui.report("交付总结", final)
    return 0


# ------------------------------------------------------------------ run (headless)

def cmd_run(args) -> int:
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    config: Config = kernel.require("config")
    workspace = str(Path(args.workspace or os.getcwd()).resolve())
    mode = _apply_mode_override(kernel, getattr(args, "mode", None), yes=args.yes)
    confirm = (lambda _p: True) if mode == "yolo" else (lambda _p: False)
    _apply_model_override(kernel, getattr(args, "model", None))
    _apply_effort_override(kernel, getattr(args, "effort", None))
    agent = create_agent(kernel, workspace, confirm=confirm)
    if mode == "yolo":
        agent.ctx.on_auto_approve = lambda name: print(f"[auto-approve] {name}", file=sys.stderr)

    def on_tool(name: str, arguments: str) -> None:
        console.print(f"  ⎿ {name} {arguments[:100]}", file=sys.stderr)

    # 后台模式: 提交后即返回, 用 qxt bg 查看进度
    if getattr(args, "bg", False):
        from ..core.background import BackgroundRunner
        runner = BackgroundRunner(kernel, config, workspace)
        if not runner.enabled:
            ui.error("后台模式已在配置中关闭 (background.enabled=false)。")
            return 1
        job = runner.submit_detached(args.task, yolo=mode == "yolo")
        ui.report("已在后台启动", f"任务: {args.task}\n\n任务 ID: {job['job_id']}\n"
              f"查看进展: [cyan]qxt bg logs {job['job_id']}[/]\n"
                  f"列出任务: [cyan]qxt bg list[/]")
        return 0

    try:
        answer = agent.run(
            args.task, stream=not args.no_stream,
            on_token=(lambda t: print(t, end="", flush=True)) if not args.no_stream else None,
            on_tool=on_tool,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[{C['err']}]任务失败: {type(exc).__name__}: {exc}[/{C['err']}]")
        return 1
    if args.no_stream:
        print(answer)
    else:
        print()
    return 0


# ------------------------------------------------------------------ agent (后台自主)

def cmd_agent(args) -> int:
    """qxt agent "任务": 提交后台自主任务, 终端立刻返回, 青小团自己在后台干活。"""
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    config: Config = kernel.require("config")
    workspace = str(Path(args.workspace or os.getcwd()).resolve())
    mode = _apply_mode_override(kernel, getattr(args, "mode", None), yes=getattr(args, "yes", False))
    confirm = (lambda _p: True) if mode == "yolo" else (lambda _p: False)
    _apply_model_override(kernel, getattr(args, "model", None))

    from ..core.background import BackgroundRunner
    runner = BackgroundRunner(kernel, config, workspace)
    if not runner.enabled:
        ui.error("后台模式已在配置中关闭 (background.enabled=false)。")
        return 1

    job = runner.submit_detached(args.task, yolo=mode == "yolo")
    ui.report("已在后台启动", f"任务: {args.task}\n\n任务 ID: {job['job_id']}\n"
              f"查看进展: [cyan]qxt bg logs {job['job_id']}[/]\n"
              f"列出任务: [cyan]qxt bg list[/] · 等待结束: [cyan]qxt bg wait {job['job_id']}[/]")
    if getattr(args, "wait", False):
        ui.info("阻塞等待任务结束 …")
        completed = runner.wait(job["job_id"])
        ui.report("后台任务结束", completed.result or completed.error or "(无输出)")
    return 0


# ------------------------------------------------------------------ bg (后台任务管理)

def cmd_bg(args) -> int:
    from ..core.background import BackgroundRunner
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    config: Config = kernel.require("config")
    workspace = str(Path(args.workspace or os.getcwd()).resolve())
    runner = BackgroundRunner(kernel, config, workspace)
    cmd = args.bg_cmd

    if cmd == "list":
        jobs = runner.list_jobs()
        if not jobs:
            ui.info("(无后台任务)")
            return 0
        table = Table(title="后台任务")
        table.add_column("ID")
        table.add_column("状态")
        table.add_column("任务")
        table.add_column("用时")
        for j in jobs:
            table.add_row(j.job_id, j.status, j.task[:40], f"{time.time() - j.started_at:.0f}s")
        console.print(table)
    elif cmd == "logs":
        job = runner.get(args.job_id)
        if job is None:
            ui.error(f"找不到后台任务: {args.job_id}")
            return 1
        for line in job.tail(args.tail):
            ui.info(line)
        ui.info(f"[状态] {job.status} · 会话流: {job.store.file}")
    elif cmd == "cancel":
        ok = runner.cancel(args.job_id)
        ui.info("已发送取消信号" if ok else "取消失败 (任务不存在或已结束)")
    elif cmd == "wait":
        job = runner.get(args.job_id)
        if job is None:
            ui.error(f"找不到后台任务: {args.job_id}")
            return 1
        completed = runner.wait(args.job_id, timeout=args.timeout or None)
        if completed is None:
            ui.error(f"后台任务消失: {args.job_id}")
            return 1
        ui.report("后台任务结束", completed.result or completed.error or "(无输出)")
    return 0


# ------------------------------------------------------------------ bench (缓存基准)

def cmd_bench(args) -> int:
    """qxt bench cache: 用固定长对话脚本实测 prompt cache 命中率。

    跑 N 轮 (默认 8): 首轮给任务, 之后固定追问"继续 / 复述上一轮结论", 模拟真实长程会话。
    每轮打印缓存命中比例, 最后给平均命中率, 直接告诉你离 dsh 99% 还有多远。
    """
    if args.bench_cmd != "cache":
        ui.error("暂仅支持: qxt bench cache")
        return 1
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    config: Config = kernel.require("config")
    workspace = str(Path(args.workspace or os.getcwd()).resolve())
    agent = create_agent(kernel, workspace, confirm=lambda _p: False)
    # 无 API Key 直接报错, 避免空跑
    if not config.api_key():
        ui.error("未配置 API Key, 无法实测缓存命中率。请先 qxt setup。")
        return 1

    rounds = max(1, args.rounds)
    followups = [
        "继续, 不要停。",
        "复述你上一轮的关键结论。",
        "再深入一步, 给出具体细节。",
        "总结到目前为止的进展。",
    ]
    ui.report("缓存基准测试", f"轮数={rounds} · 任务={args.task}\n模拟长程会话, 逐轮打印缓存命中率。")
    rates: List[float] = []
    try:
        for i in range(rounds):
            msg = args.task if i == 0 else followups[i % len(followups)]
            agent.run(msg, stream=False)
            rate = agent.cache_hit_rate()
            if rate is None:
                ui.info(f"  第 {i+1} 轮: (模型未返回缓存字段, 可能端点不支持)")
            else:
                pct = rate * 100
                mark = "✅" if pct >= 90 else ("⚠️" if pct >= 60 else "❌")
                ui.info(f"  第 {i+1} 轮: 缓存命中 {pct:.1f}% {mark}")
                rates.append(rate)
    except Exception as exc:  # noqa: BLE001
        ui.error(f"基准测试中断: {type(exc).__name__}: {exc}")
        return 1
    if rates:
        avg = sum(rates) / len(rates) * 100
        verdict = "已逼近 dsh 99% 水平 🎯" if avg >= 99 else ("达标 (≥90%)" if avg >= 90 else "仍有提升空间")
        ui.report("平均缓存命中率", f"{avg:.1f}% · {verdict}")
    else:
        ui.info("(无缓存数据, 请确认模型端点返回 prompt_cache_hit_tokens)")
    return 0


# ------------------------------------------------------------------ slash

def _handle_slash(cmd: str, agent, config: Config, workspace: str) -> bool:
    """返回 False 表示退出。"""
    registry = agent.registry
    parts = cmd.split(maxsplit=1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head in ("/exit", "/quit"):
        ui.info("再见。记忆与技能已保存。")
        return False
    if head == "/help":
        ui.info(ui.help_text)
    elif head == "/tools":
        for t in registry.tools:
            flag = " [red](需确认)[/]" if t.dangerous else ""
            ui.info(f"  [accent]{t.name}[/]{flag} [dim]·[/] {t.description[:60]}")
    elif head == "/skills":
        manager = agent.kernel.require("skill_manager")
        for s in manager.list_all():
            ui.info(f"  [cyan]{s.name}[/] (使用 {s.use_count} 次): {s.description}")
    elif head == "/memory":
        store = agent.kernel.require("memory_store")
        ui.info(store.read_memory() or "(空)")
    elif head == "/usage":
        ui.usage(agent.total_usage)
    elif head == "/model":
        # 显示当前脑子; 支持 /model switch <provider> <model> [base_url] [api_key_env] 热切换
        parts = cmd.split()
        if len(parts) >= 3 and parts[1] == "switch":
            from ..models.plugin import ModelPlugin
            provider = parts[2]
            model = parts[3] if len(parts) > 3 else config.get("model.model")
            ov = {"provider": provider, "model": model}
            if len(parts) > 4:
                ov["base_url"] = parts[4]
            if len(parts) > 5:
                ov["api_key_env"] = parts[5]
            if not _is_known_provider(provider) and "base_url" not in ov:
                ui.error(f"未知 provider {provider!r} 必须给 base_url。")
                ui.info("用法: /model switch <provider> <model> <base_url> [api_key_env]")
            else:
                ModelPlugin.switch_model(agent.kernel, ov, persist=False)
                ui.info(f"已热切换为 {provider}/{model} (当前会话生效)")
        elif len(parts) >= 2 and parts[1] in ("planner", "worker"):
            # 配置多 Agent 协作角色: /model planner <provider> <model> [base_url] [api_key_env]
            #                       /model worker  <provider> <model> [base_url] [api_key_env]
            role = parts[1]
            provider = parts[2] if len(parts) > 2 else ""
            if not provider:
                cur = config.get(f"model.{role}", {}) or {}
                ui.info(f"[{role}] provider={cur.get('provider') or '(复用主模型)'} "
                        f"model={cur.get('model') or '-'} base_url={cur.get('base_url') or '-'}")
                ui.info(f"配置: /model {role} <provider> <model> [base_url] [api_key_env]")
            else:
                ov = {"provider": provider}
                if len(parts) > 3:
                    ov["model"] = parts[3]
                if len(parts) > 4:
                    ov["base_url"] = parts[4]
                if len(parts) > 5:
                    ov["api_key_env"] = parts[5]
                config.set_user(f"model.{role}", ov)
                ui.info(f"已配置多 Agent 角色 [{role}] = {provider}/{ov.get('model', '(继承)')} "
                        f"(写盘, 下次及 /swarm 生效)")
        elif len(parts) >= 2 and parts[1] == "info":
            # /model info <provider_name> —— 显示供应商详情
            pname = parts[2] if len(parts) > 2 else ""
            if not pname:
                pname = config.get("model.provider", "")
            if pname:
                from ..models.provider_catalog import get_provider as _gp
                pp = _gp(pname)
                if pp:
                    console.print(f"  [accent]{pp.name}[/] · {pp.desc}")
                    console.print(f"  分类: {pp.category} · 地区: {'中国' if pp.region == 'cn' else '全球'}")
                    tier_labels = {1: '免费/低价', 2: '中等', 3: '高级'}
                    console.print(f"  层级: {tier_labels.get(pp.tier, str(pp.tier))} · 免费额度: {'有' if pp.free_tier else '无'}")
                    console.print(f"  端点: {pp.base_url}")
                    console.print(f"  模型: {pp.model}")
                    if pp.recommended_models:
                        console.print(f"  推荐: {', '.join(pp.recommended_models[:5])}")
                    if pp.docs_url:
                        console.print(f"  文档: {pp.docs_url}")
                else:
                    ui.info(f"供应商 '{pname}' 不在预设目录中 (可能是自定义网关)")
            else:
                ui.info("用法: /model info <供应商名>")
        else:
            cur_provider = config.get('model.provider', '')
            cur_model = config.get('model.model', '')
            ui.info(f"供应商: {cur_provider}")
            ui.info(f"模型:   {cur_model}")
            ui.info(f"网关:   {config.get('model.base_url')}")
            ui.info(f"密钥:   {config.get('model.api_key_env')} (环境变量, 不落盘)")
            # 从目录获取详细信息
            from ..models.provider_catalog import get_provider as _gp
            pp = _gp(cur_provider)
            if pp:
                tier_labels = {1: '免费/低价', 2: '中等', 3: '高级'}
                ui.info(f"层级:   {tier_labels.get(pp.tier, str(pp.tier))} · 免费额度: {'有' if pp.free_tier else '无'}")
            console.print()
            ui.info("命令:")
            ui.info("  /model info           # 查看当前供应商详情")
            ui.info("  /model info <name>    # 查看指定供应商详情")
            ui.info("  /model switch <provider> <model> [base_url] [api_key_env]  # 热切换")
            ui.info("  /model planner <provider> <model>  # 配置强模型角色")
            ui.info("  /model worker  <provider> <model>  # 配置弱模型角色")
    elif head == "/review":
        # 自动代码评审
        from ..tools.code_review import code_review as _do_review
        from ..tools.base import ToolContext
        review_path = rest.strip() if rest.strip() else ""
        ctx = ToolContext(kernel=agent.kernel, workspace=workspace, confirm=agent.ctx.confirm, yolo=agent.yolo)
        result = _do_review(ctx, path=review_path)
        ui.report("代码评审报告", result)
    elif head == "/route":
        # 智能模型路由
        from ..models.router import ModelRouter
        task_desc = rest.strip() or ui.prompt("请描述任务: ")
        if not task_desc:
            ui.info("请提供任务描述")
        else:
            router = ModelRouter(
                default_provider=config.get("model.provider", "deepseek"),
                default_model=config.get("model.model", "deepseek-chat"),
            )
            result = router.route(task_desc)
            ui.report("模型路由建议", 
                f"任务难度: {result['difficulty']}/10\n"
                f"推荐模型: {result['provider']}/{result['model']}\n"
                f"选择理由: {result['reason']}\n"
                f"预期成本: {result['cost_estimate']}")
    elif head == "/cost":
        # 成本报告
        tracker = agent.kernel.get("cost_tracker")
        if tracker:
            ui.report("成本报告", tracker.summary())
        else:
            ui.info("暂无成本记录")
    elif head == "/clear":
        agent.messages.clear()
        ui.info("上下文已清空。")
    elif head == "/more":
        ui.show_more()
    elif head == "/context":
        st = agent.context_stats()
        ui.context_bar(st)
    elif head == "/status":
        ui.dashboard(
            model=config.get("model.model", "-"),
            provider=config.get("model.provider", "-"),
            workspace=workspace,
            mode=config.mode,
            effort=config.get("agent.effort", "high"),
            stats=agent.context_stats(),
            tool_count=len(registry.tools),
        )
    elif head == "/index":
        idx = agent.kernel.get("codebase_indexer")
        if idx is None:
            from ..context.indexer import CodebaseIndexer
            idx = CodebaseIndexer(workspace)
        res = idx.build(force=True)
        ui.report("代码库地图 (已重新索引)", res.map_text())
    elif head == "/parallel":
        if not rest.strip():
            ui.info("用法: /parallel 子任务1 || 子任务2 || 子任务3  (用 || 分隔独立子任务)")
            ui.info("青小团会并发派出隔离子 Agent, 等齐结果后汇总回当前上下文。")
            return True
        subtasks = [s.strip() for s in rest.split("||") if s.strip()]
        if len(subtasks) < 2:
            ui.info("并行任务至少需要 2 个 (用 || 分隔)。单任务直接用普通对话即可。")
            return True
        from ..core.subagents import SubAgentPool, make_tasks
        pool = SubAgentPool(
            kernel=agent.kernel, config=config, workspace=workspace,
            main_agent=agent, confirm=agent.ctx.confirm,
            default_timeout=float(config.get("agent.subagent_timeout", 180)),
            isolation=config.get("agent.subagent_isolation", "process"),
        )
        ui.phase(f"并发派出 {len(subtasks)} 个子 Agent …")
        results = pool.dispatch(
            make_tasks(subtasks), stream=False,
            on_sub_tool=lambda tid, n, a: ui.tool_call(n, a),
        )
        summary = SubAgentPool.aggregate(results, title="并行调研汇总")
        # 汇总回灌主上下文, 让主 Agent 据此综合
        agent.messages.append({"role": "user", "content": f"[并行子任务结果]\n{summary}"})
        ui.report("并行汇总", summary)
    elif head == "/swarm":
        if not rest.strip():
            ui.info("用法: /swarm <一个复杂目标>  —— 青小团会自动规划并派出多 Agent 协作")
            ui.info("强模型拆解任务 -> 弱模型并发执行 (共享黑板通信) -> 强模型验收汇总。")
            ui.info("可加 '|| n' 指定子任务数量上限, 如: /swarm 写三篇开源宣传稿 || 3")
            return True
        goal = rest.strip()
        n_hint = 4
        if "||" in goal:
            goal, _, npart = goal.partition("||")
            goal = goal.strip()
            try:
                n_hint = max(2, min(int(npart.strip()), 8))
            except ValueError:
                pass
        from ..core.swarm import Swarm
        swarm = Swarm(
            kernel=agent.kernel, config=config, workspace=workspace,
            main_agent=agent, confirm=agent.ctx.confirm,
            isolation=config.get("agent.subagent_isolation", "process"),
            n_hint=n_hint,
        )
        ui.phase(f"多 Agent 协作启动 · 目标: {goal[:40]}")
        ui.info("阶段一: 强模型规划任务 …")
        collab = swarm.run(goal)
        ui.phase(f"阶段二: 弱模型并发执行 ({len(collab.worker_results)} 个) …")
        ui.phase("阶段三: 强模型验收汇总 …")
        report = collab.to_report()
        agent.messages.append({"role": "user", "content": f"[多 Agent 协作结果]\n{report}"})
        ui.report("多 Agent 协作报告", report)
    elif head == "/loop":
        if not config.get("loop.enabled", True):
            ui.error("loop 已关闭。")
            return True
        task = rest.strip() or ui.prompt("要我自主开发什么? ")

        def on_iteration(n, total, note):
            if n < 0:
                ui.phase(note)
            else:
                ui.phase(f"自主开发循环 · 第 {n}/{total} 轮")

        def on_checkpoint(report):
            return ui.ask(
                f"第 {agent.turn_count} 轮完成汇报:\n\n{report[:1500]}\n\n是否满意并结束?",
                options="[继续 C] 继续 · [完成 D] 结束 · 或直接输入反馈",
            )

        loop = DevLoop(agent, config, on_iteration=on_iteration, on_checkpoint=on_checkpoint)
        final = loop.run(
            task, stream=config.get("model.stream", True),
            on_token=lambda t: ui.stream(t),
            on_tool=lambda n, a: ui.tool_call(n, a),
            on_reason=lambda r: ui.reason(r),
            on_tool_result=lambda n, res: ui.tool_result(n, res),
            on_error=lambda msg: ui.error(msg),
        )
        console.print()
        ui.report("交付总结", final)
    else:
        ui.info(f"未知命令: {cmd}")
    return True


def _confirm_cli(prompt_text: str) -> bool:
    return ui.ask(prompt_text, options="[y] 允许 · [n] 拒绝") .strip().lower() in ("y", "yes", "允许")


def _is_known_provider(provider: str) -> bool:
    """代理到 models.is_known_provider, 供 /model 热切换时校验。"""
    from ..models import is_known_provider
    return is_known_provider(provider)


def _apply_model_override(kernel, model: Optional[str]) -> None:
    """--model 一次性覆盖: 直接改 model_adapter 的 model 字段并写回配置视图。"""
    if not model:
        return
    config = kernel.require("config")
    config.set_user("model.model", model)  # 写用户层, 下次启动也生效; 如需临时可改用 patch
    adapter = kernel.get("model_adapter")
    if adapter is not None:
        adapter.model = model
        log.info("模型已覆盖为: %s", model)


def _apply_mode_override(kernel, mode: Optional[str], yes: bool = False) -> str:
    """解析并应用运行模式: --mode 优先, --yes 等价 yolo。返回最终生效的模式名。"""
    effective = mode or ("yolo" if yes else None)
    if effective is None:
        effective = kernel.require("config").mode
    elif effective != kernel.require("config").mode:
        # 临时覆盖只改当前内存视图，不写用户配置，避免 `--yolo` 污染后续启动。
        config = kernel.require("config")
        config.data.setdefault("mode", {})["default"] = effective
    return effective


def _apply_effort_override(kernel, effort: Optional[str]) -> str:
    """解析并应用推理投入级别: --effort 优先。返回生效的级别名。

    同时把该级别对应的 temperature / loop 上限写回内核视图, 让本次会话即时生效。
    """
    cfg = kernel.require("config")
    valid = ("low", "medium", "high")
    if effort is None:
        effort = cfg.get("agent.effort", "high")
    if effort not in valid:
        effort = "high"
    _apply_effort_profile(cfg, effort)
    return effort


def _apply_effort_profile(cfg: "Config", effort: str) -> None:
    """把 effort 级别映射为具体参数 (temperature / loop 上限 / 自测)。"""
    profile = cfg.get(f"agent.effort_profiles.{effort}", {}) or {}
    if "temperature" in profile:
        cfg.set_user("model.temperature", profile["temperature"])
    if "loop_max_iter" in profile:
        cfg.set_user("loop.max_iterations", profile["loop_max_iter"])
    if "auto_test" in profile:
        cfg.set_user("loop.auto_test", profile["auto_test"])
    cfg.set_user("agent.effort", effort)


def _handle_effort(cmd: str, config: "Config", agent=None) -> Optional[str]:
    """处理 /effort [low|medium|high]。返回新的 effort 级别。"""
    parts = cmd.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
    if arg not in ("low", "medium", "high"):
        cur = config.get("agent.effort", "high")
        ui.info(f"当前 effort: {cur}  ·  用法: /effort [low|medium|high]")
        ui.info("low=快省 · medium=均衡 · high=深究(默认)")
        return None
    _apply_effort_profile(config, arg)
    # 同步改模型适配器温度 (即时生效)
    if agent is not None and hasattr(agent, "model") and hasattr(agent.model, "temperature"):
        agent.model.temperature = config.get("model.temperature", 0.7)
    ui.info(f"effort → {arg}  (temperature={config.get('model.temperature')}, "
            f"loop 上限={config.get('loop.max_iterations')}, 自测={config.get('loop.auto_test')})")
    return arg


def cmd_mode(args) -> int:
    config = Config()
    if args.value:
        if args.value == "yolo":
            ui.error("[警告] YOLO 下危险操作自动批准, 风险自担!")
        config.mode = args.value
        ui.info(f"默认模式 → {args.value}")
    else:
        cur = config.mode
        badge = "yolo · 自动批准" if cur == "yolo" else "standard · 需确认"
        ui.info(f"当前模式: {cur} ({badge})")
        ui.info("切换: [cyan]qxt mode yolo[/] / [cyan]qxt mode standard[/] · 单次: [cyan]qxt chat --mode yolo[/]")
    return 0


# ------------------------------------------------------------------ model (多供应商热切换)

def _model_overrides_from_args(args) -> dict:
    """从 model set/switch 的 CLI 参数抽取覆盖项 (base_url / api_key_env 可省略)。"""
    ov: dict = {"provider": args.provider, "model": args.model}
    if getattr(args, "base_url", None):
        ov["base_url"] = args.base_url
    if getattr(args, "api_key_env", None):
        ov["api_key_env"] = args.api_key_env
    return ov


def _model_interactive_choose(args) -> int:
    """`qxt model` 无子命令时的交互式供应商选择 (支持 48 家开箱即用)。

    分类浏览: 按 category 分组显示, 支持搜索/过滤, 按数字选择。
    选了就自动带好 base_url / 推荐模型 / 密钥变量并写入用户配置 (下次启动生效)。
    """
    from rich.table import Table as RichTable
    from rich.text import Text

    # ---- 第一步: 选择浏览模式 ----
    console.print("\n[cyan]═══ 青小团模型供应商选择 ═══[/]")
    console.print(f"[dim]共 {len(ALL_PROVIDERS)} 家供应商开箱即用, 按分类浏览或搜索:[/]")
    console.print()
    console.print("  [cyan]1.[/] 📋 按分类浏览 (推荐)")
    console.print("  [cyan]2.[/] 🔍 搜索供应商 (输入关键词)")
    console.print("  [cyan]3.[/] 🆓 只看免费层")
    console.print("  [cyan]4.[/] 🇨🇳 只看中国供应商")
    console.print("  [cyan]5.[/] 🌍 只看国际供应商")
    console.print("  [cyan]6.[/] 📝 手动输入自定义网关")
    console.print("  [cyan]7.[/] 🏠 直接选默认 (DeepSeek)")
    console.print()
    try:
        mode = input("选择模式 [1]: ").strip()
    except (EOFError, StopIteration):
        return 0
    mode = mode or "1"

    # ---- 第二步: 根据模式筛选供应商 ----
    candidates = []
    if mode == "1":
        # 默认直接展示完整供应商列表，空输入仍选择 DeepSeek；这保持了旧版
        # `qxt model` 的两次输入习惯，同时可继续使用后续分类/搜索模式。
        default_provider = get_provider("deepseek")
        candidates = ([default_provider] if default_provider else []) + [
            p for p in ALL_PROVIDERS if not default_provider or p.name != default_provider.name
        ]
    elif mode == "2":
        # 搜索
        candidates = _choose_by_search()
    elif mode == "3":
        candidates = get_free_providers()
        if not candidates:
            ui.info("没有免费层供应商")
            return 0
    elif mode == "4":
        candidates = get_cn_providers()
    elif mode == "5":
        candidates = get_global_providers()
    elif mode == "6":
        return _choose_custom_gateway(args)
    elif mode == "7":
        candidates = [get_provider("deepseek")]
    else:
        ui.error("无效选择")
        return 1

    if not candidates:
        ui.info("没有匹配的供应商")
        return 0

    # ---- 第三步: 显示候选列表并选择 ----
    _display_provider_list(candidates)
    try:
        choice = input(f"\n输入序号 [1-{len(candidates)}] 或 q 返回: ").strip()
    except EOFError:
        return 0
    if choice.lower() in ("q", "quit", "exit"):
        return 0
    try:
        idx = int(choice) if choice else 1
    except ValueError:
        ui.error("请输入数字序号。")
        return 1
    if not (1 <= idx <= len(candidates)):
        ui.error(f"序号超出范围 (1-{len(candidates)})。")
        return 1

    selected = candidates[idx - 1]

    # ---- 第四步: 确认并配置 ----
    console.print(f"\n[green]已选择:[/] [cyan]{selected.name}[/] · {selected.desc}")
    console.print(f"  模型: {selected.model}")
    console.print(f"  端点: {selected.base_url}")
    console.print(f"  密钥: {selected.api_key_env}")
    if selected.free_tier:
        console.print("  [green]✨ 该供应商有免费额度[/]")
    console.print()

    # 可选: 让用户选择模型
    if selected.recommended_models and len(selected.recommended_models) > 1:
        console.print("推荐模型:")
        for i, m in enumerate(selected.recommended_models, 1):
            default_mark = " (推荐)" if i == 1 else ""
            console.print(f"  [cyan]{i}.[/] {m}{default_mark}")
        try:
            model_choice = input(f"选择模型 [{selected.model}]: ").strip()
        except (EOFError, StopIteration):
            model_choice = ""
        if model_choice and model_choice.isdigit() and 1 <= int(model_choice) <= len(selected.recommended_models):
            selected.model = selected.recommended_models[int(model_choice) - 1]
        elif model_choice:
            selected.model = model_choice  # 用户自定义输入

    # 写回用户层配置
    config = Config(profile=args.profile, patch_file=args.patch)
    config.set_user("model.provider", selected.name)
    config.set_user("model.base_url", selected.base_url)
    config.set_user("model.model", selected.model)
    config.set_user("model.api_key_env", selected.api_key_env)
    console.print()
    ui.report("已配置模型 (写盘)", f"{selected.name}/{selected.model} @ {selected.base_url}\n"
              f"密钥变量: {selected.api_key_env} (把真实 Key 写进 ~/.qingxiaotuan/.env)\n"
              f"下次启动自动生效。立即体验: qxt chat")
    return 0


def _choose_by_category():
    """按分类浏览供应商。"""
    cats = list(PROVIDER_CATEGORIES.keys())
    console.print("\n[cyan]可用分类:[/]")
    for i, cat in enumerate(cats, 1):
        count = len(PROVIDER_CATEGORIES[cat])
        console.print(f"  [cyan]{i}.[/] {cat} [dim]({count} 家)[/]")
    console.print("  [cyan]0.[/] 返回上级")
    try:
        choice = input("选择分类: ").strip()
    except (EOFError, StopIteration):
        return []
    if choice == "0" or not choice:
        return []
    try:
        idx = int(choice)
    except ValueError:
        return []
    if 1 <= idx <= len(cats):
        return PROVIDER_CATEGORIES[cats[idx - 1]]
    return []


def _choose_by_search():
    """搜索供应商。"""
    try:
        query = input("搜索关键词 (如 deepseek / 免费 / ollama): ").strip()
    except EOFError:
        return []
    if not query:
        return []
    return search_providers(query)


def _choose_custom_gateway(args) -> int:
    """手动输入自定义网关。"""
    console.print("\n[cyan]═══ 自定义网关配置 ═══[/]")
    console.print("[dim]支持任意 OpenAI 兼容端点 (Claude 中转 / Gemini 网关 / 本地 Ollama 等)[/]")
    console.print()
    gw = input("网关名 (自定义, 如 claude-gw): ").strip() or "openai-compatible"
    model = input("模型名 (如 gpt-4o / claude-3-5-sonnet): ").strip()
    base_url = input("base_url (网关地址, 如 https://api.openai.com/v1): ").strip()
    env_name = input("密钥环境变量名 (如 OPENAI_API_KEY): ").strip() or "QXT_API_KEY"
    if not model or not base_url:
        ui.error("模型名和 base_url 不能为空。")
        return 1
    config = Config(profile=args.profile, patch_file=args.patch)
    config.set_user("model.provider", gw)
    config.set_user("model.base_url", base_url)
    config.set_user("model.model", model)
    config.set_user("model.api_key_env", env_name)
    ui.report("已配置自定义网关 (写盘)", f"{gw}/{model} @ {base_url}\n"
              f"密钥变量: {env_name}\n"
              f"下次启动自动生效。立即体验: qxt chat")
    return 0


def _display_provider_list(providers) -> None:
    """美观地显示供应商列表。"""
    from rich.table import Table as RichTable
    table = RichTable(show_header=True, header_style="cyan")
    table.add_column("#", style="cyan", width=4)
    table.add_column("名称", style="accent", width=20)
    table.add_column("推荐模型", width=30)
    table.add_column("特点", width=40)
    table.add_column("标签", width=15)
    for i, p in enumerate(providers, 1):
        tags = []
        if p.free_tier:
            tags.append("免费")
        if p.region == "cn":
            tags.append("中国")
        else:
            tags.append("全球")
        tier_label = {1: "💚低价", 2: "💙中等", 3: "🔶高级"}.get(p.tier, "")
        tags.append(tier_label)
        table.add_row(
            str(i),
            p.name,
            p.model,
            p.desc[:40],
            " ".join(tags),
        )
    console.print(table)


def _cmd_model_info(args) -> int:
    """显示某供应商的详细信息。"""
    name = args.provider_name.strip().lower()
    p = get_provider(name)
    if not p:
        # 尝试模糊搜索
        hits = search_providers(name)
        if hits:
            ui.info(f"未找到精确匹配 '{name}', 你是否想查:")
            for h in hits[:5]:
                console.print(f"  [cyan]{h.name}[/] · {h.desc[:50]}")
        else:
            ui.error(f"未知供应商: {name}")
            ui.info("用 qxt model list-providers 查看全部, 或 qxt model info <名称>")
        return 1

    console.print(f"\n[cyan]═══ 供应商详情: {p.name} ═══[/]")
    console.print()
    console.print(f"  [accent]名称:[/]     {p.name}")
    console.print(f"  [accent]描述:[/]     {p.desc}")
    console.print(f"  [accent]分类:[/]     {p.category}")
    console.print(f"  [accent]地区:[/]     {'🇨🇳 中国' if p.region == 'cn' else '🌍 全球'}")
    tier_labels = {1: '💚 免费/极低价', 2: '💙 中等', 3: '🔶 高级'}
    console.print(f"  [accent]层级:[/]     {tier_labels.get(p.tier, str(p.tier))}")
    console.print(f"  [accent]免费额度:[/] {'✨ 有' if p.free_tier else '❌ 无'}")
    console.print(f"  [accent]端点:[/]     {p.base_url}")
    console.print(f"  [accent]默认模型:[/] {p.model}")
    console.print(f"  [accent]密钥变量:[/] {p.api_key_env}")
    if p.recommended_models:
        console.print(f"  [accent]推荐模型:[/]")
        for m in p.recommended_models:
            default_mark = " (默认)" if m == p.model else ""
            console.print(f"    · {m}{default_mark}")
    if p.docs_url:
        console.print(f"  [accent]文档:[/]     {p.docs_url}")
    console.print()
    console.print("[dim]快速配置:[/]")
    console.print(f"  qxt model set {p.name} {p.model} {p.base_url} {p.api_key_env}")
    console.print(f"  或: qxt setup --quick  (交互式快速设置)")
    return 0


def cmd_model(args) -> int:
    """qxt model <current|set|switch|list-providers|info> —— 多供应商脑子管理。

    青小团不绑定任何模型: 想用 Claude/Gemini/本地网关(Ollama/vLLM)直接换。
    只要是 OpenAI 兼容的 chat/completions + tools 端点, 一套适配器通吃。
    """
    from ..models.plugin import ModelPlugin

    cmd = args.model_cmd
    if cmd is None:
        # `qxt model` 无子命令 —— 交互式挑一家 (支持 48 家开箱即用)
        return _model_interactive_choose(args)
    if cmd == "current":
        config = Config(profile=args.profile, patch_file=args.patch)
        ui.info(f"供应商: {config.get('model.provider')}")
        ui.info(f"模型:   {config.get('model.model')}")
        ui.info(f"网关:   {config.get('model.base_url')}")
        ui.info(f"密钥:   {config.get('model.api_key_env')} (从环境变量读取, 不落盘)")
        return 0
    if cmd == "list-providers":
        console.print(f"[cyan]═══ 青小团支持的模型供应商 (共 {len(ALL_PROVIDERS)} 家) ═══[/]")
        console.print()
        for cat, providers in PROVIDER_CATEGORIES.items():
            console.print(f"[cyan]── {cat} ({len(providers)} 家) ──[/]")
            for p in providers:
                free_tag = " [green]免费[/]" if p.free_tier else ""
                console.print(f"  [accent]{p.name:<18}[/] {p.model:<30}{p.desc[:30]}{free_tag}")
            console.print()
        console.print("[dim]任意 OpenAI 兼容网关也能用:[/]")
        console.print("  qxt model set <网关名> <model> <base_url> [api_key_env]")
        console.print("  qxt model info <供应商名>  # 查看详细信息")
        return 0
    if cmd == "info":
        return _cmd_model_info(args)
    if cmd in ("set", "switch"):
        kernel = build_kernel(profile=args.profile, patch_file=args.patch)
        config = kernel.require("config")
        ov = _model_overrides_from_args(args)
        provider = ov["provider"]
        if not is_known_provider(provider) and not ov.get("base_url"):
            ui.error(f"未知 provider {provider!r} 必须提供 base_url。")
            ui.info(f"用法: qxt model {cmd} {provider} <model> <base_url> [api_key_env]")
            return 1
        if cmd == "set":
            # 写回用户层配置: 下次启动默认生效
            for k, v in ov.items():
                config.set_user(f"model.{k}", v)
            ui.report("已切换模型 (写盘)", f"{provider}/{ov['model']}"
                      + (f" @ {ov['base_url']}" if ov.get('base_url') else "")
                      + "\n下次启动自动生效。当前会话要立即生效用: qxt model switch ...")
        else:
            # 仅运行时热切换: 重建适配器并重新注册内核服务, 不写盘
            ModelPlugin.switch_model(kernel, ov, persist=False)
            ui.report("已热切换 (当前会话)", f"{provider}/{ov['model']}"
                      + (f" @ {ov['base_url']}" if ov.get('base_url') else "")
                      + "\n仅本次会话生效, 未写盘。")
        return 0
    ui.error("未知 model 子命令")
    return 1


# ------------------------------------------------------------------ setup / doctor

def cmd_setup(args) -> int:
    """Hermes 风格三模式引导: 快速 / 完整 / 空白。

    --quick: 跳过模式选择, 直接进入快速设置 (适合脚本/CI)。
    快速模式: 一步选供应商 + 填 Key, 30 秒搞定。
    完整模式: 逐项配置所有选项。
    空白模式: 最小化默认配置, 全部手动后续调整。
    """
    config = Config()
    config.ensure_home()

    # --quick 标志: 跳过模式选择菜单
    if getattr(args, "quick", False):
        return _setup_quick(config)

    console.print("\n[cyan]═══════════════════════════════════════════════[/]")
    console.print("[cyan]   青小团 CLI · 首次设置向导[/]")
    console.print("[cyan]═══════════════════════════════════════════════[/]")
    console.print()
    console.print("选择设置模式:")
    console.print("  [cyan]1.[/] ⚡ 快速设置 [dim](推荐, 30 秒搞定)[/]")
    console.print("      选一家供应商 → 填 API Key → 完成")
    console.print("  [cyan]2.[/] 🔧 完整设置 [dim](高级用户)[/]")
    console.print("      逐项配置: 供应商/模型/网关/密钥/模式/工具")
    console.print("  [cyan]3.[/] 📭 空白设置 [dim](最小配置)[/]")
    console.print("      仅创建家目录, 默认 DeepSeek, 稍后手动调整")
    console.print()
    try:
        choice = input("选择模式 [1]: ").strip()
    except EOFError:
        return 0
    mode = choice or "1"

    if mode == "1":
        return _setup_quick(config)
    elif mode == "2":
        return _setup_full(config)
    elif mode == "3":
        return _setup_blank(config)
    else:
        ui.error("无效选择")
        return 1


def _setup_quick(config) -> int:
    """快速设置: 选供应商 → 填 Key → 完成。"""
    console.print("\n[cyan]═══ 快速设置 ═══[/]")
    console.print("[dim]选择一家供应商, 填入 API Key 即可开始。[/]")
    console.print()

    # 列出推荐供应商 (免费 + 主流)
    recommended = [
        get_provider("deepseek"),
        get_provider("opencode-zen"),
        get_provider("qwen"),
        get_provider("openai"),
        get_provider("gemini"),
        get_provider("moonshot"),
        get_provider("zhipu"),
        get_provider("doubao"),
        get_provider("groq"),
        get_provider("local"),
    ]
    recommended = [p for p in recommended if p is not None]

    console.print("推荐供应商:")
    for i, p in enumerate(recommended, 1):
        free_tag = " [green]免费[/]" if p.free_tier else ""
        console.print(f"  [cyan]{i:>2}.[/] {p.name:<15} {p.desc[:40]}{free_tag}")
    console.print(f"  [cyan]{len(recommended)+1:>2}.[/] 查看全部 {len(ALL_PROVIDERS)} 家供应商")
    console.print()
    try:
        choice = input(f"选择供应商 [1]: ").strip()
    except EOFError:
        return 0
    idx = int(choice) if choice else 1
    if idx == len(recommended) + 1:
        # 跳转到完整模式的供应商选择
        return _setup_quick_browse_all(config)
    if not (1 <= idx <= len(recommended)):
        ui.error("序号超出范围")
        return 1
    selected = recommended[idx - 1]

    # 让用户选择模型
    if selected.recommended_models and len(selected.recommended_models) > 1:
        console.print(f"\n{selected.name} 可用模型:")
        for i, m in enumerate(selected.recommended_models, 1):
            mark = " (推荐)" if i == 1 else ""
            console.print(f"  [cyan]{i}.[/] {m}{mark}")
        try:
            model_choice = input(f"选择模型 [{selected.model}]: ").strip()
        except (EOFError, StopIteration):
            model_choice = ""
        if model_choice and model_choice.isdigit() and 1 <= int(model_choice) <= len(selected.recommended_models):
            selected.model = selected.recommended_models[int(model_choice) - 1]
        elif model_choice:
            selected.model = model_choice

    # 填 API Key
    console.print(f"\n[dim]密钥环境变量: {selected.api_key_env}[/]")
    console.print(f"[dim]免费额度: {'有 ✨' if selected.free_tier else '无'}[/]")
    console.print()
    api_key = getpass.getpass(f"API Key ({selected.api_key_env}, 留空则稍后用环境变量): ").strip()

    # 写配置
    config.set_user("model.provider", selected.name)
    config.set_user("model.base_url", selected.base_url)
    config.set_user("model.model", selected.model)
    config.set_user("model.api_key_env", selected.api_key_env)

    # 写 .env
    _save_api_key(config, selected.api_key_env, api_key)

    # 初始化记忆和技能
    _init_first_run(config)

    console.print()
    console.print("[green]✅ 设置完成![/]")
    console.print(f"  供应商: {selected.name}/{selected.model}")
    console.print(f"  端点: {selected.base_url}")
    if api_key:
        console.print(f"  密钥: 已保存到 ~/.qingxiaotuan/.env")
    else:
        console.print(f"  密钥: 请稍后设置环境变量 {selected.api_key_env}")
    console.print()
    console.print("[cyan]开始使用:[/]")
    console.print("  qxt chat                 # 交互对话")
    console.print("  qxt dev \"你的任务\"       # 自主开发循环")
    console.print("  qxt run \"任务\"           # 一次性任务")
    console.print("  qxt --tui                # 全屏 TUI 工作台")
    console.print()
    return 0


def _setup_quick_browse_all(config) -> int:
    """快速设置中浏览全部供应商。"""
    candidates = []
    console.print("\n[cyan]全部供应商 (按分类):[/]")
    for cat, providers in PROVIDER_CATEGORIES.items():
        console.print(f"\n  [cyan]── {cat} ({len(providers)} 家) ──[/]")
        for p in providers:
            free_tag = " [green]免费[/]" if p.free_tier else ""
            console.print(f"    [cyan]{p.name:<18}[/] {p.model:<30} {p.desc[:35]}{free_tag}")
    console.print()
    try:
        name = input("输入供应商名称: ").strip()
    except EOFError:
        return 0
    selected = get_provider(name)
    if not selected:
        ui.error(f"未知供应商: {name}")
        return 1

    if selected.recommended_models and len(selected.recommended_models) > 1:
        console.print(f"\n{selected.name} 可用模型:")
        for i, m in enumerate(selected.recommended_models, 1):
            mark = " (推荐)" if i == 1 else ""
            console.print(f"  [cyan]{i}.[/] {m}{mark}")
        try:
            model_choice = input(f"选择模型 [{selected.model}]: ").strip()
        except EOFError:
            model_choice = ""
        if model_choice and model_choice.isdigit() and 1 <= int(model_choice) <= len(selected.recommended_models):
            selected.model = selected.recommended_models[int(model_choice) - 1]

    api_key = getpass.getpass(f"API Key ({selected.api_key_env}, 留空则稍后设置): ").strip()
    config.set_user("model.provider", selected.name)
    config.set_user("model.base_url", selected.base_url)
    config.set_user("model.model", selected.model)
    config.set_user("model.api_key_env", selected.api_key_env)
    _save_api_key(config, selected.api_key_env, api_key)
    _init_first_run(config)
    console.print("\n[green]✅ 设置完成![/] 立即开始: [cyan]qxt chat[/]")
    return 0


def _setup_full(config) -> int:
    """完整设置: 逐项配置。"""
    console.print("\n[cyan]═══ 完整设置 ═══[/]")
    console.print("[dim]逐项配置所有选项, 适合高级用户。[/]")
    console.print()

    # 1. 选择供应商
    provider = input("provider [deepseek]: ").strip() or "deepseek"
    base_url = input("base_url [https://api.deepseek.com]: ").strip() or "https://api.deepseek.com"
    model = input("model [deepseek-chat]: ").strip() or "deepseek-chat"
    env_name = input("密钥环境变量名 [DEEPSEEK_API_KEY]: ").strip() or "DEEPSEEK_API_KEY"

    # 2. API Key
    api_key = getpass.getpass("API Key (留空则稍后用环境变量): ").strip()

    # 3. 运行模式
    console.print("\n运行模式:")
    console.print("  [cyan]1.[/] standard [dim](默认, 危险操作需确认)[/]")
    console.print("  [cyan]2.[/] yolo [dim](自动批准, 适合沙箱环境)[/]")
    try:
        mode_choice = input("选择模式 [1]: ").strip()
    except EOFError:
        mode_choice = "1"
    mode = "yolo" if mode_choice == "2" else "standard"

    # 4. 推理投入
    console.print("\n推理投入:")
    console.print("  [cyan]1.[/] high [dim](深度思考, 默认)[/]")
    console.print("  [cyan]2.[/] medium [dim](均衡)[/]")
    console.print("  [cyan]3.[/] low [dim](快速省 token)[/]")
    try:
        effort_choice = input("选择投入级别 [1]: ").strip()
    except EOFError:
        effort_choice = "1"
    effort_map = {"1": "high", "2": "medium", "3": "low"}
    effort = effort_map.get(effort_choice, "high")

    # 5. 写配置
    config.set_user("model.provider", provider)
    config.set_user("model.base_url", base_url)
    config.set_user("model.model", model)
    config.set_user("model.api_key_env", env_name)
    config.set_user("mode.default", mode)
    config.set_user("agent.effort", effort)

    _save_api_key(config, env_name, api_key)
    _init_first_run(config)

    console.print("\n[green]✅ 完整设置完成![/]")
    console.print(f"  供应商: {provider}/{model}")
    console.print(f"  模式: {mode} · 投入: {effort}")
    console.print("\n[cyan]开始使用:[/]")
    console.print("  qxt chat                 # 交互对话")
    console.print("  qxt dev \"你的任务\"       # 自主开发循环")
    console.print("  qxt model                # 切换供应商")
    return 0


def _setup_blank(config) -> int:
    """空白设置: 最小配置。"""
    console.print("\n[cyan]═══ 空白设置 ═══[/]")
    console.print("[dim]创建最小配置, 默认使用 DeepSeek。后续可随时调整。[/]")
    console.print()

    api_key = getpass.getpass("API Key (可选, 留空则稍后配置): ").strip()
    _save_api_key(config, "DEEPSEEK_API_KEY", api_key)
    _init_first_run(config)

    console.print("\n[green]✅ 空白设置完成![/]")
    console.print("  默认供应商: deepseek/deepseek-chat")
    console.print("  如需切换: qxt model")
    console.print("\n[cyan]开始使用:[/] qxt chat")
    return 0


def _save_api_key(config, env_name: str, api_key: str) -> None:
    """保存 API Key 到 .env 文件。"""
    if not api_key:
        return
    from ..config import _chmod_600
    env_file = config.home / ".env"
    lines = []
    if env_file.exists():
        lines = [ln for ln in env_file.read_text(encoding="utf-8").splitlines()
                 if not ln.startswith(f"{env_name}=")]
    lines.append(f"{env_name}={api_key}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _chmod_600(env_file)
    os.environ[env_name] = api_key
    console.print(f"[dim]密钥已保存到 {env_file} (权限 600)[/]")


def _init_first_run(config) -> None:
    """首次运行初始化: seed 技能、创建记忆库。"""
    try:
        seed_builtin_skills(config)
        console.print("[dim]已初始化技能库和记忆库[/]")
    except Exception:  # noqa: BLE001
        pass


def cmd_doctor(_args) -> int:
    config = Config()
    load_dotenv(config.home)  # 让 doctor 也能看到 .env 里的密钥
    config = Config()  # 重新构造以刷新 api_key 解析
    key = config.api_key()
    checks = [
        ("Python", f"[green]{sys.version.split()[0]}[/]"),
        ("家目录", f"[green]{config.home}[/]" if config.home.exists() else "[yellow]未创建 (qxt setup)[/]"),
        ("API Key", "[green]已配置[/]" if key else "[red]未配置[/]"),
        ("模型", f"{config.get('model.provider')}/{config.get('model.model')}"),
        ("连接", f"base_url={config.get('model.base_url')} timeout={config.get('model.timeout')}s"),
    ]
    skills_dir = config.home / "skills"
    n_skills = len(list(skills_dir.glob("*.md"))) if skills_dir.exists() else 0
    checks.append(("技能", str(n_skills)))
    try:
        kernel = build_kernel()
        checks.append(("内核",
                       f"[green]OK[/] {len(kernel.plugins)}插件 {len(kernel.services)}服务 "
                       f"{len(kernel.require('tool_registry').tools)}工具"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("内核", f"[red]失败: {exc}[/]"))
    if key:
        try:
            import urllib.request
            url = str(config.get("model.base_url", "")).rstrip("/") + "/models"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                ok = resp.status == 200
            checks.append(("连通", "[green]可达[/]" if ok else "[yellow]非200[/]"))
        except Exception as exc:  # noqa: BLE001
            checks.append(("连通", f"[yellow]探测失败: {type(exc).__name__}[/]"))
    for name, status in checks:
        console.print(f"  [cyan]{name}[/] [dim]·[/] {status}")
    return 0


# ------------------------------------------------------------------ config / plugin / skill / memory / cron

def cmd_config(args) -> int:
    config = Config(profile=args.profile, patch_file=args.patch)
    if args.config_cmd == "dump":
        print(dump_yaml(config.data))
    elif args.config_cmd == "dump-default":
        print(dump_yaml(DEFAULT_CONFIG))
    elif args.config_cmd == "validate":
        from ..config.validate import validate

        errors, warnings = validate(config)
        console.print(f"[dim]校验配置: {config.user_config_path}[/]")
        for w in warnings:
            console.print(f"  [yellow]⚠ {w}[/]")
        for e in errors:
            console.print(f"  [red]✗ {e}[/]")
        if not errors and not warnings:
            console.print("  [green]✓ 配置无误[/]")
        elif not errors:
            console.print(f"  [green]✓ 通过 (含 {len(warnings)} 条提示)[/]")
        else:
            console.print(f"  [red]✗ 发现 {len(errors)} 个错误, 请修正后再运行[/]")
            return 1
        return 0
    elif args.config_cmd == "profiles":
        from ..config import PRESET_PROFILES
        if not PRESET_PROFILES:
            ui.info("当前无内置预设 profile。可用 `qxt --profile <name>` 指定任意目录级 profile。")
        for name, prof in PRESET_PROFILES.items():
            m = prof.get("model", {})
            ui.info(f"[cyan]{name}[/]  ->  {m.get('provider')}/{m.get('model')}  ({m.get('base_url')})")
        ui.info("\n使用: `qxt --profile opencode-zen chat`")
    elif args.config_cmd == "get":
        print(dump_yaml({args.key: config.get(args.key)}))
    elif args.config_cmd == "set":
        value = args.value
        for cast in (int, float):
            try:
                value = cast(value)
                break
            except (ValueError, TypeError):
                continue
        if value in ("true", "false"):
            value = value == "true"
        config.set_user(args.key, value)
        ui.info(f"已设置 {args.key} = {value}")
    return 0


def cmd_plugin(args) -> int:
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    for name, plugin in kernel.plugins.items():
        provides = ", ".join(plugin.provides) or "-"
        console.print(f"  [cyan]{name}[/] [dim]v{plugin.version}[/] [dim]·[/] {provides}")
    console.print(f"[dim]服务: {', '.join(kernel.services)}[/]")


def cmd_session(args) -> int:
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    store = kernel.get("session_store")
    if store is None:
        ui.error("会话存储未启用")
        return 1
    if args.session_cmd == "list":
        sessions = store.list_sessions()
        if not sessions:
            ui.info("暂无会话记录 (~/.qingxiaotuan/sessions)")
            return 0
        console.print(f"[dim]最近的会话 (共 {len(sessions)} 个):[/]")
        for i, s in enumerate(sessions[:20], 1):
            meta = SessionStore.read_meta(s)
            if meta and meta.get("kind") == "background":
                badge = f"[yellow]后台[/] {meta.get('job_id', '')}"
                title = meta.get("task", "")[:50].replace("\n", " ")
            else:
                badge = "[cyan]交互[/]"
                title = SessionStore.peek_title(s)
            console.print(f"  [cyan]{i}.[/] {badge} [dim]{s.name}[/]  {title}")
        return 0
    elif args.session_cmd == "resume":
        sessions = store.list_sessions()
        if not sessions:
            ui.error("没有可恢复的会话")
            return 1
        # resume 接受序号或文件名; 默认恢复最近一个
        target = args.target
        chosen = None
        if target:
            if target.isdigit() and 1 <= int(target) <= len(sessions):
                chosen = sessions[int(target) - 1]
            else:
                for s in sessions:
                    if s.name == target or target in s.name:
                        chosen = s
                        break
        else:
            chosen = sessions[0]
        if chosen is None:
            ui.error(f"未找到会话: {target}")
            return 1
        ui.info(f"恢复会话: {chosen.name}")
        args.resume = str(chosen)
        return cmd_chat(args)
    return 0


def _peek_session_title(path: Path) -> str:
    """读取会话首条 user 事件作为预览标题。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "user" and "message" in rec:
                    content = rec["message"].get("content", "")
                    return content[:60].replace("\n", " ")
    except OSError:
        pass
    return ""


def cmd_skill(args) -> int:
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    manager = kernel.require("skill_manager")
    if args.skill_cmd == "list":
        for s in manager.list_all():
            ui.info(f"[cyan]{s.name}[/] [dim]({s.use_count}次)[/] [dim]·[/] {s.description}")
    elif args.skill_cmd == "show":
        slug = args.name.lower().replace(" ", "-")
        skill = manager.load(slug)
        if skill:
            console.print(Markdown(f"# {skill.name}\n\n{skill.body}"))
        else:
            ui.error(f"技能不存在: {args.name}")
    return 0


def cmd_memory(args) -> int:
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    store = kernel.require("memory_store")
    if args.memory_cmd == "list":
        ui.info(store.read_memory() or "(空)")
    elif args.memory_cmd == "search":
        hits = store.search(args.query, limit=10)
        for h in hits:
            ui.info(f"  [cyan]·[/] {h['content'][:200]} [dim]({h['source']})[/]")
        if not hits:
            ui.info("无结果")
    return 0


def cmd_cron(args) -> int:
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    config = kernel.require("config")
    store = kernel.get("cron_store")
    if store is None:
        ui.error("cron 插件未启用")
        return 1
    if args.cron_cmd == "add":
        job = store.add(args.name, args.prompt, args.interval)
        ui.info(f"已添加任务 {job['id']}: {args.name} (每 {args.interval} 分钟)")
    elif args.cron_cmd == "list":
        for j in store.list():
            ui.info(f"[cyan]{j['id']}[/] {j['name']} 每 {j['interval_minutes']} 分钟 {'✅' if j['enabled'] else '⏸'}")
    elif args.cron_cmd == "remove":
        ok = store.remove(args.id)
        ui.info("已删除" if ok else "未找到该任务")
    elif args.cron_cmd == "tick":
        from ..cron.runner import run_due_jobs
        workspace = str(Path(args.workspace or os.getcwd()).resolve())
        n = run_due_jobs(kernel, config, workspace, config.home, stream=False,
                         on_job=lambda job, res: ui.info(
                             f"{'✅' if res['ok'] else '❌'} {job['name']}: "
                             f"{res['error'] or res['output'][:80]}"))
        ui.info(f"执行了 {n} 个到期任务" if n else "没有到期任务")
        return 0
    elif args.cron_cmd == "start":
        return _cmd_cron_start(kernel, config, args)
    return 0


def _cmd_cron_start(kernel, config, args) -> int:
    """常驻调度守护: 每隔 --check 秒检查一次到期任务并执行。

    --detach: 后台分离运行, 写 PID 文件, 立即返回终端 (适合开机自启)。
    非 detach 模式: 前台阻塞运行, Ctrl+C 退出。
    """
    import signal
    import threading

    from ..cron.runner import run_due_jobs

    workspace = str(Path(args.workspace or os.getcwd()).resolve())
    check = max(5, int(args.check))
    home = config.home
    pid_file = home / "cron" / "daemon.pid"
    stop = threading.Event()

    def _loop() -> None:
        ui.info(f"[cron 守护] 启动 · 每 {check}s 检查 · 工作区 {workspace}")
        while not stop.is_set():
            try:
                run_due_jobs(kernel, config, workspace, home, stream=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("cron 守护循环异常 (已忽略): %s", exc)
            # 分段 sleep, 响应 stop 更快
            for _ in range(check):
                if stop.wait(1):
                    break

    if getattr(args, "detach", False):
        # 简单分离: 用 daemon 线程 + 写 PID, 父进程退出后子线程随进程结束。
        # Windows 下用 subprocess 重新拉起一个无终端的 python 进程更稳妥。
        import subprocess
        import sys
        detached = subprocess.Popen(
            [sys.executable, "-m", "qingxiaotuan", "cron", "start",
             "--workspace", workspace, "--check", str(check)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pid_file.write_text(str(detached.pid), encoding="utf-8")
        ui.info(f"[cron 守护] 已在后台启动 (PID {detached.pid}) · PID 文件 {pid_file}")
        ui.info("停止: 任务管理器结束该进程, 或删除 PID 文件后重启。")
        return 0

    def _on_int(signum, frame):
        ui.info("\n[cron 守护] 收到退出信号, 正在停止…")
        stop.set()

    signal.signal(signal.SIGINT, _on_int)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_int)
    try:
        _loop()
    finally:
        stop.set()
        if pid_file.exists():
            try:
                pid_file.unlink()
            except OSError:
                pass
    return 0


def cmd_mcp(args) -> int:
    kernel = build_kernel(profile=args.profile, patch_file=args.patch)
    config = kernel.require("config")
    servers = config.get("mcp.servers", []) or []
    if args.mcp_cmd == "list":
        if not servers:
            ui.info("未配置 MCP server。在 config.yaml 加:")
            ui.info('  mcp:\n    servers:\n      - name: fs\n        command: npx\n'
                    '        args: ["-y", "@modelcontextprotocol/server-filesystem", "."]')
            return 0
        registry = kernel.require("tool_registry")
        mcp_tools = [t for t in registry.tools if t.name.startswith("mcp__")]
        for s in servers:
            ui.info(f"  [cyan]{s.get('name', s['command'])}[/] [dim]{s['command']} {' '.join(s.get('args', []))}[/]")
        ui.info(f"\n桥接工具 {len(mcp_tools)} 个:")
        for t in mcp_tools:
            ui.info(f"  [magenta]{t.name}[/] [dim]·[/] {t.description[:60]}")
    return 0
