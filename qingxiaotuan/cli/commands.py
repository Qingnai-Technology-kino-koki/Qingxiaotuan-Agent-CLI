"""qxt 各子命令实现 + 内部辅助函数 (override / slash / effort 处理)。"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from rich.table import Table

from .. import __version__
from ..app import build_kernel, create_agent, seed_builtin_skills
from ..config import DEFAULT_CONFIG, Config, dump_yaml, home_dir, load_dotenv, to_yaml_str
from ..models import PROVIDER_PRESETS, KNOWN_PROVIDERS, is_known_provider
from ..models.provider_catalog import (
    ALL_PROVIDERS, PROVIDER_CATEGORIES, get_provider, get_provider_models,
    search_providers, get_free_providers, get_cn_providers, get_global_providers,
)
from ..logging_conf import log
from ..core.devloop import DevLoop
from ..i18n import (choose_language_interactive, ensure_language, normalize_language,
                    set_language, t)
from ..hooks import HookManager, HOOK_EVENTS
from ..ui import FullScreenTUI, UI
from ..ui.repl import C

from ..ui.plain_console import console

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
        console.print("会话存储未初始化")
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
            if p.stem.startswith(arg):
                return _load_session_into_agent(agent, p)
    console.print(f"未找到会话: {arg}")
    return None


def _list_sessions(agent) -> None:
    """列出最近会话 (编号 + 标题 + 时间), 供 /resume 选择。"""
    store = agent.session
    if store is None:
        console.print("会话存储未初始化")
        return
    from ..memory.sessions import SessionStore
    sessions = store.list_sessions()[:10]
    if not sessions:
        console.print("没有历史会话")
        return
    import datetime
    for i, p in enumerate(sessions, 1):
        title = SessionStore.peek_title(p)
        ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
        console.print(f"  {i:2d}. {p.stem}  {ts}  {title}")
    console.print("输入 /resume <编号|会话id|latest> 恢复")


def _auto_first_run() -> bool:
    """首次运行检测: 若家目录不存在或无 API Key, 自动引导快速设置。

    返回 True 表示已执行设置 (调用方应重新加载配置)。
    """
    home = home_dir()
    if (home / "config.yaml").exists():
        return False
    console.print("首次使用青小团? 让我们快速设置一下…")
    return cmd_setup(type("Args", (), {"quick": True})()) == 0


def cmd_chat(args) -> int:
    """交互式对话 (默认命令)。"""
    # 首次使用先走快速设置向导 (REPL 与 TUI 两分支共用), 写盘后再建内核, 新配置即时生效
    _auto_first_run()
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1

    config = kernel.require("config")
    # 首次交互且为双向 TTY 时选择界面语言; 已配置则直接应用 (非交互环境绝不阻塞)
    ensure_language(config)

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
    # 会话级 Hooks: SessionStart (agent.ctx.hooks 已在 create_agent 注入)
    _run_session_hooks(agent, "SessionStart", {
        "workspace": workspace,
        "model": config.get("model.model"),
        "provider": config.get("model.provider"),
    })
    seed_builtin_skills(kernel)

    # 会话恢复 (对标 Claude Code --continue/--resume)
    resume_arg = getattr(args, "resume", None)
    if resume_arg:
        loaded = _resume_session(agent, resume_arg)
        if loaded is None:
            return 1
        console.print(f"{t('chat.resumed', path=loaded, count=len(agent.messages))}")

    # --print 非交互模式 (对标 Claude Code --print): 从 stdin/参数读取任务, 跑完输出到 stdout
    is_print = getattr(args, "print_mode", False)
    if is_print:
        # 从命令行或 stdin 读取任务
        task_text = getattr(args, "task", "") or ""
        if not task_text and not sys.stdin.isatty():
            task_text = sys.stdin.read().strip()
        if not task_text:
            print("[错误] --print 模式需要提供任务描述 (qxt --print chat \"任务\") 或通过 stdin 输入")
            return 1
        max_cost = getattr(args, "max_cost", 0.0)
        if max_cost > 0:
            config.set_user("router.budget_limit", max_cost)
        def _print_token(t: str) -> None:
            sys.stdout.write(t)
            sys.stdout.flush()
        answer = agent.run(task_text, stream=not getattr(args, "no_stream", False), on_token=_print_token)
        if answer:
            print(answer)
        # 费用报告
        usage = agent.total_usage
        prompt_t = usage.get("prompt_tokens", 0)
        compl_t = usage.get("completion_tokens", 0)
        cache_hit = usage.get("prompt_cache_hit_tokens", 0)
        cost = getattr(agent, '_session_cost', None)
        cost_str = f" | cost=${cost:.4f}" if cost else ""
        print(f"\n--- tokens: {prompt_t}+{compl_t}, cache_hit={cache_hit}{cost_str} ---", file=sys.stderr)
        return 0

    # 全屏 TUI 模式
    if getattr(args, "tui", False):
        def submit_in_tui(text: str) -> Optional[str]:
            events: list[str] = []

            def on_tool(name: str, arguments: str) -> None:
                tui.set_mascot("working")
                events.append(t("tui.tool_event", name=name, args=arguments[:120]))

            answer = agent.run(
                text, stream=False,
                on_tool=on_tool,
                on_tool_result=lambda name, result: events.append(
                    t("tui.result_event", name=name, result=result[:240])),
                on_error=lambda message: events.append(message),
            )
            for event in events:
                tui.append_log(event)
            return answer or t("tui.no_output")

        def command_in_tui(text: str) -> Optional[str]:
            if text.strip() in ("/exit", "/quit"):
                tui.close()
                return t("tui.exiting")
            handled = _handle_slash(text, agent, config, workspace)
            tui.set_plan_mode(agent.plan_mode)
            return t("tui.cmd_done") if handled else t("tui.exiting")

        tui = FullScreenTUI(submit_in_tui, on_cancel=agent.cancel, on_command=command_in_tui)
        tui.append_log(f"{t('tui.model_label')}: "
                       f"{config.get('model.provider')}/{config.get('model.model')}")
        tui.append_log(f"{t('tui.workspace_label')}: {workspace}")
        if not config.api_key():
            tui.append_log(t("tui.no_key"))
        tui.run()
        return 0

    # 普通 REPL 模式
    try:
        rc = _run_chat_repl(agent, config, workspace, mode, effort)
    finally:
        # 会话级 Hooks: SessionEnd (无论正常退出还是被中断, 都尽量触发)
        _run_session_hooks(agent, "SessionEnd", {"workspace": workspace})
    return rc


def _run_session_hooks(agent, event: str, payload: Optional[Dict[str, Any]]) -> None:
    """触发会话级 Hooks (SessionStart/SessionEnd), 异常隔离、不影响主流程。"""
    ctx = getattr(agent, "ctx", None)
    hooks = getattr(ctx, "hooks", None) if ctx is not None else None
    if hooks is not None:
        try:
            hooks.run_session(event, payload)
        except Exception as exc:  # noqa: BLE001
            log.debug("会话钩子 %s 执行失败: %s", event, exc)


def _run_chat_repl(agent, config: Config, workspace: str, mode: str, effort: str) -> int:
    """普通 REPL 对话循环 (cmd_chat 与 session resume 共用)。"""
    ui.banner(config, workspace, f"{config.get('model.provider')}/{config.get('model.model')}",
              mode=mode, effort=effort)
    ui.status_bar(mode, effort, workspace)
    if not config.api_key():
        console.print(f"{t('chat.no_api_key')}")

    while True:
        try:
            user_input = ui.prompt()
        except (EOFError, KeyboardInterrupt):
            console.print("\n再见！")
            break
        if not user_input:
            continue
        if user_input.strip() in ("/exit", "/quit"):
            console.print("再见！")
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

    def on_tool(name: str, arguments: str) -> None:
        _close_reasoning()
        ui.tool_call(name, _parse_args(arguments))

    answer = agent.run(
        user_input,
        stream=stream if stream is not None else config.get("model.stream", True),
        on_token=lambda t: ui.stream(t),
        on_tool=on_tool,
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
        ui.set_context_pct(st["estimated_tokens"] / st["budget_tokens"] * 100,
                           used=st["estimated_tokens"], budget=st["budget_tokens"])
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
        console.print("请提供任务描述, 例如: qxt dev \"给项目加一个 CSV 导出功能\"")
        return 1
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
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
        console.print(f"\n检查点: {result[:200]}")
        try:
            reply = input("  继续 / 调整方向 / done: ").strip()
        except (EOFError, KeyboardInterrupt):
            reply = "done"
        return reply

    loop = DevLoop(agent, config, on_checkpoint=on_checkpoint)
    console.print(f"开始自主开发: {task}")
    out = loop.run(task, stream=config.get("model.stream", True))
    console.print(f"\n完成: {out[:500]}")
    return 0


# ------------------------------------------------------------------ run (headless)

def cmd_run(args) -> int:
    """headless 一次性任务。"""
    task = getattr(args, "task", "")
    if not task:
        console.print("请提供任务描述")
        return 1
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
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

    # 硬预算控制 (对标 Claude Code --max-cost): 超过上限自动停止
    max_cost = getattr(args, "max_cost", 0.0)
    if max_cost > 0:
        config.set_user("router.budget_limit", max_cost)

    if getattr(args, "bg", False):
        from ..core.background import submit_background
        job_id = submit_background(kernel, agent, task, workspace)
        console.print(f"已提交后台任务: {job_id}")
        return 0

    def on_token(t: str) -> None:
        sys.stdout.write(t)
        sys.stdout.flush()

    answer = agent.run(
        task,
        stream=not getattr(args, "no_stream", False),
        on_token=on_token,
    )
    if answer:
        console.print(f"\n{answer}")
    # 预算报告 (有上限时显示)
    if max_cost > 0:
        spent = agent.total_usage.get("_cost_usd", 0.0)
        print(f"\n[预算] 上限 ${max_cost:.2f} | 已用 ${spent:.4f} | 剩余 ${max_cost - spent:.4f}")
    return 0


# ------------------------------------------------------------------ agent (background)

def cmd_agent(args) -> int:
    """后台自主任务。"""
    task = getattr(args, "task", "")
    if not task:
        console.print("请提供任务描述")
        return 1
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
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
    console.print(f"已提交后台任务: {job_id}")
    if getattr(args, "wait", False):
        from ..core.background import wait_for_job
        console.print("等待任务完成…")
        result = wait_for_job(kernel, job_id)
        console.print(f"任务完成: {result[:500]}")
    return 0


# ------------------------------------------------------------------ bg

def cmd_bg(args) -> int:
    """后台任务管理。"""
    bg_cmd = getattr(args, "bg_cmd", None)
    if not bg_cmd:
        console.print("用法: qxt bg list|logs|cancel|wait")
        return 1
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    from ..core.background_store import BackgroundStore
    store = BackgroundStore(config.home)
    if bg_cmd == "list":
        jobs = store.list()
        if not jobs:
            console.print("没有后台任务")
        else:
            for j in jobs:
                console.print(f"  {j.get('job_id','?')[:8]}  {j.get('status','?')}  {j.get('task','')[:60]}")
    elif bg_cmd == "logs":
        job = store.get(getattr(args, "job_id", ""))
        if job:
            console.print(json.dumps(job, indent=2, ensure_ascii=False)[:2000])
        else:
            console.print("任务未找到")
    elif bg_cmd == "cancel":
        store.update(getattr(args, "job_id", ""), status="cancelled")
        console.print("已请求取消")
    elif bg_cmd == "wait":
        from ..core.background import wait_for_job
        result = wait_for_job(kernel, getattr(args, "job_id", ""), timeout=getattr(args, "timeout", 0))
        console.print(f"{result[:500]}")
    return 0


# ------------------------------------------------------------------ bench

def cmd_bench(args) -> int:
    """基准测试。"""
    bench_cmd = getattr(args, "bench_cmd", None)
    if bench_cmd == "cache":
        return _bench_cache(args)
    if bench_cmd == "latency":
        return _bench_latency(args)
    console.print("bench 功能开发中…")
    return 0


# 基准测试排除的写类/有副作用工具: 保证基准只读, 不污染工作区
_BENCH_EXCLUDE_TOOLS = (
    "write_file", "edit_file", "delete_file", "delete_dir", "move_file",
    "skill_save", "memory_write", "memory_update_user", "run_shell",
)


def _bench_cache(args) -> int:
    """实测 prompt cache 命中率: 固定长对话脚本多轮调用, 统计命中/成本。"""
    rounds = max(1, getattr(args, "rounds", 8))
    task = getattr(args, "task", "解释一下当前工作区的结构")

    try:
        kernel = build_kernel()
    except Exception as exc:  # noqa: BLE001
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    provider = config.get("model.provider", "?")
    model = config.get("model.model", "?")
    if not config.api_key():
        console.print("未配置 API Key, 无法调用模型。请先运行 qxt setup 或 qxt model 配置。")
        return 1

    agent = create_agent(
        kernel, os.getcwd(),
        confirm=lambda prompt: True,
        exclude_tools=_BENCH_EXCLUDE_TOOLS,
    )

    console.print("缓存命中率基准测试")
    console.print(f"  模型: {provider}/{model}")
    console.print(f"  轮次: {rounds}")
    console.print(f"  首轮任务: {task}\n")

    rows: List[tuple] = []
    errors = 0
    prev: Dict[str, int] = {}
    for i in range(rounds):
        prompt = task if i == 0 else (
            f"继续。请基于以上对话，深入展开第 {i + 1} 部分，补充新的细节和见解。"
        )
        t0 = time.time()
        try:
            agent.run(prompt, stream=False)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            console.print(f"  第 {i + 1} 轮失败: {exc}")
            continue
        elapsed = time.time() - t0
        # total_usage 是累计值; 逐轮明细展示「本轮的增量」更符合直觉
        cur = dict(agent.total_usage)
        delta = {k: cur.get(k, 0) - prev.get(k, 0) for k in cur}
        prev = cur
        rows.append((i + 1, delta, elapsed))

    if not rows:
        console.print("所有轮次均失败, 无法统计。")
        return 1

    total_prompt = sum(r[1].get("prompt_tokens", 0) for r in rows)
    total_completion = sum(r[1].get("completion_tokens", 0) for r in rows)
    total_hit = sum(r[1].get("prompt_cache_hit_tokens", 0) for r in rows)
    total_miss = sum(r[1].get("prompt_cache_miss_tokens", 0) for r in rows)
    hit_rate = total_hit / (total_hit + total_miss) if (total_hit + total_miss) > 0 else 0.0

    table = Table(title="逐轮明细")
    table.add_column("轮次", justify="right")
    table.add_column("耗时(s)", justify="right")
    table.add_column("prompt", justify="right")
    table.add_column("输出", justify="right")
    table.add_column("缓存命中", justify="right")
    table.add_column("缓存未命中", justify="right")
    table.add_column("命中率", justify="right")
    for idx, usage, elapsed in rows:
        hit = usage.get("prompt_cache_hit_tokens", 0)
        miss = usage.get("prompt_cache_miss_tokens", 0)
        rate = hit / (hit + miss) if (hit + miss) > 0 else 0.0
        table.add_row(
            str(idx), f"{elapsed:.1f}",
            f"{usage.get('prompt_tokens', 0):,}",
            f"{usage.get('completion_tokens', 0):,}",
            f"{hit:,}", f"{miss:,}", f"{rate:.1%}",
        )
    console.print(table)

    from ..models.router import estimate_cost
    est_cost = estimate_cost(provider, model, total_prompt, total_completion)
    console.print("\n汇总")
    console.print(f"  总 prompt: {total_prompt:,}")
    console.print(f"  总输出:   {total_completion:,}")
    console.print(f"  缓存命中: {total_hit:,} ({hit_rate:.1%})")
    console.print(f"  缓存未命中: {total_miss:,}")
    if errors:
        console.print(f"  失败轮次: {errors}")
    console.print(f"  估算成本: ${est_cost:.4f}")
    return 0


def _bench_latency(args) -> int:
    """实测模型响应延迟与吞吐: 多轮最小请求, 统计 avg/min/max/p95 + tokens/s。"""
    rounds = max(1, getattr(args, "rounds", 5))
    task = getattr(args, "task", "ping")

    try:
        kernel = build_kernel()
    except Exception as exc:  # noqa: BLE001
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    provider = config.get("model.provider", "?")
    model = config.get("model.model", "?")
    if not config.api_key():
        console.print("未配置 API Key, 无法调用模型。请先运行 qxt setup 或 qxt model 配置。")
        return 1

    from ..models import create_adapter
    try:
        adapter = create_adapter(config)
    except Exception as exc:  # noqa: BLE001
        console.print(f"适配器创建失败: {exc}")
        return 1

    console.print("延迟基准测试")
    console.print(f"  模型: {provider}/{model}")
    console.print(f"  轮次: {rounds}")
    console.print(f"  请求: {task!r}\n")

    latencies: List[float] = []
    tokens = 0
    errors = 0
    for i in range(rounds):
        t0 = time.time()
        try:
            resp = adapter.chat([{"role": "user", "content": task}], stream=False)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            console.print(f"  第 {i + 1} 轮失败: {exc}")
            continue
        elapsed = time.time() - t0
        latencies.append(elapsed)
        usage = getattr(resp, "usage", None) or {}
        tokens += usage.get("completion_tokens", 0)
        console.print(f"  第 {i + 1} 轮: {elapsed:.2f}s")

    if not latencies:
        console.print("所有轮次均失败, 无法统计。")
        return 1

    latencies.sort()
    avg = sum(latencies) / len(latencies)
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    total_time = sum(latencies)
    throughput = tokens / total_time if total_time > 0 else 0.0

    console.print("\n汇总")
    console.print(f"  成功轮次: {len(latencies)}/{rounds}" + (f" (失败 {errors})" if errors else ""))
    console.print(f"  平均延迟: {avg:.2f}s")
    console.print(f"  最小延迟: {latencies[0]:.2f}s")
    console.print(f"  最大延迟: {latencies[-1]:.2f}s")
    console.print(f"  P95 延迟: {p95:.2f}s")
    console.print(f"  总输出 token: {tokens:,}")
    console.print(f"  吞吐: {throughput:.1f} tokens/s")
    return 0


# ------------------------------------------------------------------ setup

def cmd_setup(args) -> int:
    """初始化向导。"""
    # 安装即选语言: 未配置且处于交互 TTY 时弹十语言编号菜单, 写盘后不再询问
    kernel = build_kernel()
    config = kernel.require("config")
    ensure_language(config)

    console.print(f"{t('setup.title')}")
    console.print(f"  1. {t('setup.opt_quick')}")
    console.print(f"  2. {t('setup.opt_full')}")
    console.print(f"  3. {t('setup.opt_blank')}")
    try:
        choice = input(t("setup.choose")).strip() or "1"
    except (EOFError, KeyboardInterrupt):
        choice = "1"

    if choice == "1":
        return _setup_quick(config)
    if choice == "2":
        return _setup_full(config)
    if choice == "3":
        return _setup_blank(config)
    console.print(f"{t('setup.wip')}")
    return 0


def _setup_quick(config) -> int:
    """快速设置: 供应商 + 模型 + API Key。"""
    console.print(f"\n{t('setup.quick_title')}")
    console.print(f"  {t('setup.providers')}")
    for i, name in enumerate(list(PROVIDER_PRESETS.keys())[:12], 1):
        p = PROVIDER_PRESETS[name]
        console.print(f"    {i:2d}. {name:<20s} {p.get('desc','')[:40]}")
    try:
        idx = int(input(t("setup.pick_number")).strip() or "1") - 1
    except (ValueError, EOFError, KeyboardInterrupt):
        idx = 0
    names = list(PROVIDER_PRESETS.keys())
    provider = names[min(idx, len(names) - 1)]
    preset = get_provider(provider) or ALL_PROVIDERS[0]
    console.print(f"\n  {t('setup.chosen', provider=provider)}")
    console.print(f"  {t('setup.gateway', url=preset.base_url)}")
    provider, model_name = _pick_model_interactive(preset)
    preset = get_provider(provider) or preset
    api_key_env = preset.api_key_env
    if api_key_env:
        try:
            key = input(t("setup.api_key_prompt", env=api_key_env)).strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        if key:
            _save_api_key(api_key_env, key)

    config.set_user("model.provider", provider)
    config.set_user("model.model", model_name)
    if preset.base_url:
        config.set_user("model.base_url", preset.base_url)
    if api_key_env:
        config.set_user("model.api_key_env", api_key_env)
    console.print(
        f"\n{t('setup.done', model=f'{provider}/{model_name}')}")
    return 0


def _save_api_key(env_name: str, key: str) -> None:
    """把密钥追加写入 ~/.qingxiaotuan/.env。"""
    env_path = home_dir() / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\n{env_name}={key}\n")
    console.print(f"  {t('setup.key_saved', path=env_path)}")


def _ask_choice(prompt: str, options: List[str], default: str) -> str:
    """通用枚举选择: 回车用默认, 非法输入回退默认。"""
    try:
        raw = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not raw:
        return default
    return raw if raw in options else default


def _setup_full(config) -> int:
    """完整设置: 供应商/模型/API Key + 运行模式/推理投入/温度/输出上限。"""
    console.print(f"\n{t('setup.full_title')}")
    console.print(f"  {t('setup.providers')}")
    for i, name in enumerate(list(PROVIDER_PRESETS.keys())[:12], 1):
        p = PROVIDER_PRESETS[name]
        console.print(f"    {i:2d}. {name:<20s} {p.get('desc','')[:40]}")
    try:
        idx = int(input(t("setup.pick_number")).strip() or "1") - 1
    except (ValueError, EOFError, KeyboardInterrupt):
        idx = 0
    names = list(PROVIDER_PRESETS.keys())
    provider = names[min(idx, len(names) - 1)]
    preset = get_provider(provider) or ALL_PROVIDERS[0]
    console.print(f"\n  {t('setup.chosen', provider=provider)}")
    console.print(f"  {t('setup.gateway', url=preset.base_url)}")
    provider, model_name = _pick_model_interactive(preset)
    preset = get_provider(provider) or preset
    api_key_env = preset.api_key_env
    if api_key_env:
        try:
            key = input(t("setup.api_key_prompt", env=api_key_env)).strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        if key:
            _save_api_key(api_key_env, key)

    mode = _ask_choice(t("setup.mode_prompt"), ["standard", "yolo"], "standard")
    effort = _ask_choice(t("setup.effort_prompt"), ["low", "medium", "high"], "high")
    try:
        temp = float(input(t("setup.temp_prompt")).strip() or "0.7")
        temp = max(0.0, min(2.0, temp))
    except (ValueError, EOFError, KeyboardInterrupt):
        temp = 0.7
    try:
        mt = int(input(t("setup.max_tokens_prompt")).strip() or "8192")
        mt = max(1, mt)
    except (ValueError, EOFError, KeyboardInterrupt):
        mt = 8192

    config.set_user("model.provider", provider)
    config.set_user("model.model", model_name)
    if preset.base_url:
        config.set_user("model.base_url", preset.base_url)
    if api_key_env:
        config.set_user("model.api_key_env", api_key_env)
    config.set_user("model.temperature", temp)
    config.set_user("model.max_tokens", mt)
    config.set_user("mode.default", mode)
    config.set_user("agent.effort", effort)
    console.print(
        f"\n{t('setup.done', model=f'{provider}/{model_name}')}")
    console.print(
        f"  {t('setup.full_summary', mode=mode, effort=effort, temp=temp, mt=mt)}")
    return 0


def _setup_blank(config) -> int:
    """空白设置: 仅创建目录骨架, 不配置模型。"""
    config.ensure_home()
    console.print(f"\n{t('setup.blank_title')}")
    console.print(f"  {t('setup.blank_created')}")
    console.print(f"  {t('setup.blank_hint')}")
    for line in t("setup.blank_commands").split("\n"):
        console.print(f"    {line}")
    return 0


# ------------------------------------------------------------------ doctor

def cmd_doctor(args) -> int:
    """环境健康检查: 配置 / 引擎 / 网络 / 模型连通 / 工作区, 分级输出。"""
    issues: List[tuple] = []

    def mark(level: str) -> str:
        return {"ok": "✓", "warn": "!", "err": "✗"}[level]

    console.print("环境检查")
    console.print(f"  Python: {sys.version.split()[0]} ({sys.executable})")
    console.print(f"  版本: {__version__}")

    try:
        kernel = build_kernel()
    except Exception as exc:  # noqa: BLE001
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")

    # ---- 配置健康 (复用 config validate 的分级校验) ----
    console.print("\n配置")
    for level, section, msg in _validate_config(config):
        console.print(f"  {mark(level)} {section}: {msg}")
        if level in ("warn", "err"):
            issues.append((level, section, msg))

    # ---- 引擎健康 ----
    console.print("\n引擎")
    try:
        from ..ext.registry import engine_healthcheck
        results = engine_healthcheck()
        ok_count = sum(1 for v in results.values() if v.get("ok"))
        for name, v in results.items():
            console.print(f"  {mark('ok' if v.get('ok') else 'err')} {name}")
        if ok_count < len(results):
            issues.append(("err", "engines", f"{ok_count}/{len(results)} 就绪"))
    except Exception:  # noqa: BLE001
        console.print("  ✗ 无法检测")
        issues.append(("err", "engines", "无法检测"))

    # ---- 网络连通 (对配置/预设的 base_url 做轻量探测, 失败仅警告) ----
    console.print("\n网络")
    base_url = config.get("model.base_url", "") or ""
    if not base_url:
        preset = get_provider(config.get("model.provider", ""))
        base_url = (preset.base_url if preset else "") or ""
    if base_url:
        host = base_url.split("//")[-1].split("/")[0]
        try:
            import httpx
            t0 = time.time()
            with httpx.Client(timeout=3.0) as client:
                client.head(base_url, follow_redirects=True)
            console.print(f"  {mark('ok')} {host} 可达 ({time.time() - t0:.0f}ms)")
        except Exception as exc:  # noqa: BLE001
            console.print(f"  {mark('warn')} {host} 不可达: {exc}")
            issues.append(("warn", "network", f"{host} 不可达"))
    else:
        console.print(f"  {mark('warn')} 未设置 base_url, 跳过网络检查")
        issues.append(("warn", "network", "未设置 base_url"))

    # ---- 模型连通 (需要 API Key; 失败仅警告, 不阻塞 doctor) ----
    console.print("\n模型")
    if not config.api_key():
        console.print(f"  {mark('warn')} 未配置 API Key, 跳过连通性测试")
        issues.append(("warn", "model", "未配置 API Key"))
    else:
        provider = config.get("model.provider", "?")
        model = config.get("model.model", "?")
        try:
            from ..models import create_adapter
            adapter = create_adapter(config)
            t0 = time.time()
            adapter.chat([{"role": "user", "content": "ping"}], stream=False)
            console.print(f"  {mark('ok')} {provider}/{model} 连通 ({time.time() - t0:.1f}s)")
        except Exception as exc:  # noqa: BLE001
            console.print(f"  {mark('warn')} {provider}/{model} 连通失败: {exc}")
            issues.append(("warn", "model", f"{provider}/{model} 连通失败"))

    # ---- 工作区 git 状态 ----
    console.print("\n工作区")
    workspace = getattr(args, "workspace", None) or os.getcwd()
    try:
        r = subprocess.run(
            ["git", "-C", workspace, "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            dirty = len([l for l in r.stdout.splitlines() if l.strip()])
            console.print(f"  {mark('ok')} git 仓库 ({'有未提交改动' if dirty else '干净'})")
        else:
            console.print(f"  {mark('warn')} 非 git 仓库")
            issues.append(("warn", "workspace", "非 git 仓库"))
    except Exception:  # noqa: BLE001
        console.print(f"  {mark('warn')} git 检测失败")
        issues.append(("warn", "workspace", "git 检测失败"))

    # ---- 汇总 ----
    errs = [i for i in issues if i[0] == "err"]
    warns = [i for i in issues if i[0] == "warn"]
    if errs:
        console.print(f"\n发现 {len(errs)} 个错误, {len(warns)} 个警告")
        return 1
    if warns:
        console.print(f"\n发现 {len(warns)} 个警告")
        return 0
    console.print("\n全部正常")
    return 0


# ------------------------------------------------------------------ mode

def cmd_mode(args) -> int:
    """查看/切换默认运行模式。"""
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    value = getattr(args, "value", None)
    if value:
        config.set_user("mode.default", value)
        console.print(f"默认模式已切换为: {value}")
    else:
        current = config.get("mode.default", "standard")
        console.print(f"  当前默认模式: {current}")
    return 0


# ------------------------------------------------------------------ model


def _show_provider_models(name: str) -> None:
    """打印某供应商的可选模型清单 (qxt model list <provider>)。"""
    info = get_provider(name)
    if info is None:
        console.print(f"未知供应商: {name}")
        return
    models = info.recommended_models
    console.print(f"\n{info.name} 可选模型 ({len(models)}):")
    if not models:
        console.print("  (未收录, 可手动输入模型名)")
        return
    for i, m in enumerate(models, 1):
        console.print(f"  {i:3d}. {m}")


def _strip_model_label(m: str) -> str:
    """剥离模型清单项的 |free/|paid 标注, 返回纯模型 ID。"""
    return m.split("|")[0] if "|" in m else m


def _pick_api_key_interactive(preset) -> str:
    """提示用户输入 API Key, 返回 Key 字符串 (未输入则返回空串)。"""
    env_name = preset.api_key_env
    if not env_name:
        return ""
    existing = os.environ.get(env_name, "")
    if existing:
        console.print(f"  已检测到 {env_name} (长度 {len(existing)})")
        try:
            use = input("  使用此 Key? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            use = ""
        if use != "n":
            return existing
    try:
        return input(f"  输入 {preset.name} API Key (回车跳过): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _pick_model_interactive(preset) -> tuple:
    """展示供应商模型清单并让用户选择, 返回 (provider, model)。

    支持输入 search <关键词> / s:<关键词> 跨供应商搜索模型。
    """
    models = preset.recommended_models
    if not models:
        try:
            raw = input(f"  输入模型名 (回车用默认 {preset.model}): ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        return preset.name, (raw or preset.model)
    console.print(f"\n  选择模型 ({preset.name}, 共 {len(models)} 个):")
    for i, m in enumerate(models, 1):
        console.print(f"    {i:3d}. {m}")
    console.print("    提示: 输入 search <关键词> 可跨供应商搜索模型")
    try:
        raw = input("    选择编号 [1] (或输入自定义模型名 / search <关键词>): ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    low = raw.lower()
    if low.startswith("search ") or low.startswith("s:"):
        query = raw[7:].strip() if low.startswith("search ") else raw[2:].strip()
        picked = _search_and_pick_model(query)
        if picked:
            return picked
        return preset.name, _strip_model_label(models[0])
    if not raw:
        return preset.name, _strip_model_label(models[0])
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(models):
            return preset.name, _strip_model_label(models[idx])
        console.print(f"  编号超出范围, 已选默认 {models[0]}")
        return preset.name, _strip_model_label(models[0])
    return preset.name, raw


def _search_and_pick_model(query: str) -> Optional[tuple]:
    """跨供应商搜索模型, 返回 (provider, model) 或 None (取消)。"""
    from ..models.provider_catalog import search_models
    results = search_models(query)
    if not results:
        console.print(f"  未找到匹配的模型: {query}")
        return None
    console.print(f"\n  搜索结果 ({len(results)}):")
    for i, (prov, m, free) in enumerate(results[:20], 1):
        tag = "免费" if free else ""
        console.print(f"    {i:3d}. {prov:<16s} {m} {tag}")
    if len(results) > 20:
        console.print(f"    … 还有 {len(results) - 20} 条, 请用更精确的关键词")
    try:
        raw = input("    选择编号 (回车取消): ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(results):
            return results[idx][0], results[idx][1]
    return None


def _find_models(query: str, config) -> int:
    """跨供应商搜索模型, 选择后写入配置 (qxt model find <关键词>)。"""
    from ..models.provider_catalog import search_models
    results = search_models(query)
    if not results:
        console.print(f"未找到匹配的模型: {query}")
        return 1
    console.print(f"\n搜索结果 ({len(results)}):")
    for i, (prov, m, free) in enumerate(results[:20], 1):
        tag = "免费" if free else ""
        console.print(f"  {i:3d}. {prov:<16s} {m} {tag}")
    if len(results) > 20:
        console.print(f"  … 还有 {len(results) - 20} 条, 请用更精确的关键词")
    try:
        raw = input("  选择编号 (回车取消): ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    if not raw.isdigit():
        console.print("已取消")
        return 0
    idx = int(raw) - 1
    if not (0 <= idx < len(results)):
        console.print(f"编号超出范围")
        return 1
    prov, m, _ = results[idx]
    preset = get_provider(prov)
    config.set_user("model.provider", prov)
    config.set_user("model.model", m)
    if preset and preset.base_url:
        config.set_user("model.base_url", preset.base_url)
    if preset and preset.api_key_env:
        config.set_user("model.api_key_env", preset.api_key_env)
    console.print(f"已选择: {prov}/{m}")
    return 0


class _ConfigOverride:
    """只读配置视图: 在基础 config 上覆盖若干键 (不写盘, 供 model test 临时测试任意端点)。"""

    def __init__(self, base, overrides: Dict[str, Any]):
        self._base = base
        self._overrides = overrides

    def get(self, dotted: str, default: Any = None) -> Any:
        if dotted in self._overrides:
            return self._overrides[dotted]
        return self._base.get(dotted, default)

    def api_key(self) -> Optional[str]:
        env_name = self.get("model.api_key_env", "DEEPSEEK_API_KEY")
        key = os.environ.get(env_name)
        if key:
            return key
        return cast(Optional[str], self._base.api_key())


def _model_test(config, args=None) -> int:
    """连通性测试: 向当前配置的端点发一条最小请求, 验证 base_url + API Key + 模型名。

    args 可携带 --provider/--model/--base-url/--api-key-env 临时覆盖 (不写盘)。
    """
    overrides: Dict[str, Any] = {}
    for attr, dotted in (("provider", "model.provider"), ("model", "model.model"),
                         ("base_url", "model.base_url"), ("api_key_env", "model.api_key_env")):
        val = getattr(args, attr, None) if args is not None else None
        if val:
            overrides[dotted] = val
    if overrides:
        config = _ConfigOverride(config, overrides)

    provider = config.get("model.provider", "?")
    model = config.get("model.model", "?")
    base_url = config.get("model.base_url", "")
    if not config.api_key():
        console.print(f"未配置 API Key — 无法测试。请先运行 qxt model 或 qxt setup。")
        return 1
    console.print(f"  端点: {base_url or '(未设置)'}")
    console.print(f"  模型: {provider}/{model}")
    try:
        from ..models import create_adapter
        adapter = create_adapter(config)
        t0 = time.time()
        resp = adapter.chat(
            [{"role": "user", "content": "ping"}],
            stream=False,
        )
        elapsed = time.time() - t0
    except Exception as exc:  # noqa: BLE001
        console.print(f"  连接失败: {exc}")
        return 1
    usage = getattr(resp, "usage", None) or {}
    console.print(f"  连接成功 ({elapsed:.1f}s)")
    content = (resp.content or "")[:120]
    if content:
        console.print(f"  回复: {content}")
    if usage:
        console.print(
            f"  token: 入 {usage.get('prompt_tokens', 0)} / 出 {usage.get('completion_tokens', 0)}")
    return 0


def cmd_model(args) -> int:
    """配置/热切换模型供应商。"""
    model_cmd = getattr(args, "model_cmd", None)
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")

    if model_cmd == "current":
        provider = config.get("model.provider", "?")
        model = config.get("model.model", "?")
        base_url = config.get("model.base_url", "")
        env_name = config.get("model.api_key_env", "")
        key = config.api_key()
        console.print(f"  provider:    {provider}")
        console.print(f"  model:       {model}")
        console.print(f"  base_url:    {base_url or '(未设置)'}")
        console.print(f"  api_key_env: {env_name or '(未设置)'}")
        if key:
            console.print(f"  API Key: 已配置 (长度 {len(key)})")
        else:
            console.print(f"  API Key: 未配置 — 调用模型会失败")
        console.print(f"  temperature: {config.get('model.temperature', 0.7)}")
        console.print(f"  max_tokens:  {config.get('model.max_tokens', 8192)}")
        console.print(f"  切换: qxt model 或 qxt model set <provider> <model>")
    elif model_cmd == "test":
        return _model_test(config, args)
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
        # 未显式给 base_url / api_key_env 时, 用已知供应商预设自动补全
        preset = get_provider(provider) if provider else None
        if base_url:
            config.set_user("model.base_url", base_url)
        elif preset and preset.base_url:
            config.set_user("model.base_url", preset.base_url)
        if api_key_env:
            config.set_user("model.api_key_env", api_key_env)
        elif preset and preset.api_key_env:
            config.set_user("model.api_key_env", preset.api_key_env)
        console.print(f"模型已切换为: {provider or '?'}/{model_name or '?'}")
    elif model_cmd == "switch":
        from ..models.plugin import ModelPlugin
        overrides = {}
        for key in ("provider", "model", "base_url", "api_key_env"):
            val = getattr(args, key, None)
            if val:
                overrides[key] = val
        ModelPlugin.switch_model(kernel, overrides)
        # 用户显式切模型 → 暂停自动路由, 尊重其选择
        config.set_user("router.auto_switch", False)
        console.print(f"模型已热切换 (当前会话生效, 自动路由已暂停)。")
    elif model_cmd == "list":
        _show_provider_models(getattr(args, "provider_name", ""))
    elif model_cmd == "info":
        name = getattr(args, "provider_name", "")
        info = get_provider(name)
        if info:
            console.print(f"  {info.name}: {info.desc}")
            console.print(f"  默认模型: {info.model}")
            console.print(f"  base_url: {info.base_url}")
            models = info.recommended_models
            if models:
                console.print(f"  可选模型 ({len(models)}):")
                for i, m in enumerate(models, 1):
                    console.print(f"    {i:3d}. {m}")
            else:
                console.print("  可选模型: (未收录, 可手动输入)")
        else:
            console.print(f"未知供应商: {name}")
    elif model_cmd == "find":
        return _find_models(getattr(args, "query", ""), config)
    elif model_cmd == "list-providers":
        for cat_name, providers in PROVIDER_CATEGORIES.items():
            console.print(f"\n{cat_name}")
            for p in providers:
                console.print(f"  {p.name:<20s} {p.desc[:50]}")
    else:
        # 无子命令: 交互式选择 (分类 → 供应商 → 模型)
        console.print(f"选择模型供应商")
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
            chosen_provider, model_name = _pick_model_interactive(chosen)
            if chosen_provider != chosen.name:
                chosen = get_provider(chosen_provider) or chosen
            api_key = _pick_api_key_interactive(chosen)
            config.set_user("model.provider", chosen.name)
            config.set_user("model.model", model_name)
            if chosen.base_url:
                config.set_user("model.base_url", chosen.base_url)
            if chosen.api_key_env:
                config.set_user("model.api_key_env", chosen.api_key_env)
            if api_key:
                os.environ[chosen.api_key_env] = api_key
                console.print(f"  已设置 {chosen.api_key_env} (本次会话生效)")
            console.print(f"已选择: {chosen.name}/{model_name}")
    return 0


# ------------------------------------------------------------------ config

def _validate_config(config) -> List[tuple]:
    """校验配置, 返回 [(level, section, msg)]; level ∈ ok/warn/err。"""
    issues: List[tuple] = []

    def ok(section: str, msg: str) -> None:
        issues.append(("ok", section, msg))

    def warn(section: str, msg: str) -> None:
        issues.append(("warn", section, msg))

    def err(section: str, msg: str) -> None:
        issues.append(("err", section, msg))

    # ---- model ----
    provider = config.get("model.provider", "")
    model = config.get("model.model", "")
    base_url = config.get("model.base_url", "")
    if not provider:
        err("model.provider", "未设置供应商")
    elif is_known_provider(provider):
        ok("model.provider", f"{provider} (已知供应商)")
    elif base_url:
        warn("model.provider", f"{provider} 为自定义网关, 已提供 base_url")
    else:
        err("model.provider", f"{provider} 不是内置供应商且未配置 base_url")

    if not model:
        err("model.model", "未设置模型名")
    else:
        ok("model.model", model)

    if base_url:
        if base_url.startswith(("http://", "https://")):
            ok("model.base_url", base_url)
        else:
            err("model.base_url", f"不是合法 URL: {base_url}")
    else:
        warn("model.base_url", "未显式设置 (将使用供应商预设)")

    temp = config.get("model.temperature", 0.7)
    if isinstance(temp, (int, float)) and 0 <= temp <= 2:
        ok("model.temperature", f"{temp} (范围 0~2)")
    else:
        err("model.temperature", f"应为 0~2 的数字, 当前: {temp!r}")

    mt = config.get("model.max_tokens", 0)
    if isinstance(mt, int) and mt > 0:
        ok("model.max_tokens", str(mt))
    else:
        err("model.max_tokens", f"应为正整数, 当前: {mt!r}")

    env_name = config.get("model.api_key_env", "DEEPSEEK_API_KEY")
    if config.api_key():
        ok("API Key", f"已配置 ({env_name})")
    else:
        err("API Key", f"未配置密钥 (期望环境变量 {env_name})")

    # ---- mode ----
    mode = config.get("mode.default", "standard")
    if mode in ("standard", "yolo"):
        ok("mode.default", mode)
    else:
        err("mode.default", f"应为 standard/yolo, 当前: {mode!r}")

    # ---- agent ----
    effort = config.get("agent.effort", "high")
    if effort in ("low", "medium", "high"):
        ok("agent.effort", effort)
    else:
        err("agent.effort", f"应为 low/medium/high, 当前: {effort!r}")

    mi = config.get("agent.max_iterations", 0)
    if isinstance(mi, int) and mi > 0:
        ok("agent.max_iterations", str(mi))
    else:
        err("agent.max_iterations", f"应为正整数, 当前: {mi!r}")

    # ---- context ----
    budget = config.get("context.budget_tokens", 0)
    if isinstance(budget, (int, float)) and budget > 0:
        ok("context.budget_tokens", f"{budget:,}")
    else:
        err("context.budget_tokens", f"应为正数, 当前: {budget!r}")

    return issues


def cmd_config(args) -> int:
    """配置管理。"""
    config_cmd = getattr(args, "config_cmd", None)
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    if config_cmd == "dump":
        print(to_yaml_str(config.data))
    elif config_cmd == "dump-default":
        print(to_yaml_str(DEFAULT_CONFIG))
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
        console.print(f"{key} = {value}")
    elif config_cmd == "validate":
        issues = _validate_config(config)
        console.print("配置校验")
        errs = warns = 0
        for level, section, msg in issues:
            if level == "ok":
                console.print(f"  ✓ {section:<22s} {msg}")
            elif level == "warn":
                warns += 1
                console.print(f"  ! {section:<22s} {msg}")
            else:
                errs += 1
                console.print(f"  ✗ {section:<22s} {msg}")
        if errs or warns:
            console.print(f"\n结果: {errs} 错误, {warns} 警告")
            return 1 if errs else 0
        console.print("\n结果: 全部通过 ✓")
        return 0
    elif config_cmd == "profiles":
        console.print("可用 profiles: default")
    else:
        console.print("用法: qxt config dump|get|set|validate|profiles")
    return 0


# ------------------------------------------------------------------ plugin

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
        console.print(f"启动失败: {exc}")
        return 1
    manager = kernel.get("skill_manager")
    if not manager:
        console.print("技能管理器未初始化")
        return 1
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


# ------------------------------------------------------------------ memory

def cmd_memory(args) -> int:
    """记忆管理。"""
    memory_cmd = getattr(args, "memory_cmd", None)
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    store = kernel.get("memory_store")
    if not store:
        console.print("记忆存储未初始化")
        return 1
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


# ------------------------------------------------------------------ cron

def _cron_pid_file(config) -> Path:
    return cast(Path, config.home / "cron" / "daemon.pid")


def _cron_daemon_alive(config) -> bool:
    """守护进程是否存活 (PID 文件 + 跨平台进程探测)。"""
    from ..core.background_store import _is_process_alive
    pid_file = _cron_pid_file(config)
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return _is_process_alive(pid)


def _cron_spawn_daemon(config, workspace: str, check: int) -> int:
    """以独立子进程拉起 cron 守护 (Windows DETACHED_PROCESS), 返回 PID。"""
    env = dict(os.environ)
    env["QXT_HOME"] = str(config.home)
    creationflags = 0
    if os.name == "nt":
        creationflags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0))
    proc = subprocess.Popen(
        [sys.executable, "-m", "qingxiaotuan.cron.daemon",
         "--check", str(check), "--workspace", workspace, "--home", str(config.home)],
        cwd=workspace, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return proc.pid


def cmd_cron(args) -> int:
    """定时任务管理 (add/list/remove/tick/start/stop/status)。"""
    try:
        kernel = build_kernel()
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    store = kernel.get("cron_store")
    if store is None:
        console.print("定时任务存储未初始化 (cron.enabled=false?)")
        return 1

    cmd = getattr(args, "cron_cmd", None)

    if cmd == "add":
        job = store.add(args.name, args.prompt, args.interval)
        output = getattr(args, "output", None)
        if output:
            store.update(job["id"], output=str(Path(output).expanduser()))
            job = store.get(job["id"])
        console.print(f"已添加定时任务: {job['id']} ({job['name']}) 每 {job['interval_minutes']} 分钟")
        if job.get("output"):
            console.print(f"结果将写入: {job['output']}")
        console.print("启动守护 (qxt cron start) 或执行 qxt cron tick 即可触发")
        return 0

    if cmd == "list":
        jobs = store.list()
        if not jobs:
            console.print("没有定时任务")
            return 0
        table = Table(title="定时任务", show_header=True)
        table.add_column("ID")
        table.add_column("状态")
        table.add_column("名称")
        table.add_column("间隔")
        table.add_column("上次执行")
        for job in jobs:
            status = "✅" if job.get("enabled", True) else "⏸"
            last = (datetime.datetime.fromtimestamp(job["last_run"]).strftime("%m-%d %H:%M")
                    if job.get("last_run") else "从未")
            table.add_row(job["id"], status, job.get("name", ""),
                          f"{job['interval_minutes']} 分钟", last)
        console.print(table)
        return 0

    if cmd == "remove":
        ok = store.remove(args.id)
        if ok:
            console.print(f"已删除任务 {args.id}")
        else:
            console.print(f"未找到任务 {args.id}")
        return 0 if ok else 1

    if cmd in ("enable", "disable"):
        enabled = cmd == "enable"
        if store.set_enabled(args.id, enabled):
            state = "已启用" if enabled else "已暂停"
            console.print(f"任务 {args.id} {state}")
            return 0
        console.print(f"未找到任务 {args.id}")
        return 1

    if cmd == "edit":
        job = store.get(args.id)
        if job is None:
            console.print(f"未找到任务 {args.id}")
            return 1
        fields: Dict[str, Any] = {}
        if getattr(args, "interval", None):
            fields["interval_minutes"] = args.interval
        if getattr(args, "prompt", None):
            fields["prompt"] = args.prompt
        if getattr(args, "name", None):
            fields["name"] = args.name
        if getattr(args, "output", None) is not None:
            fields["output"] = str(Path(args.output).expanduser()) if args.output else ""
        if not fields:
            console.print("未提供要修改的字段 (--interval/--prompt/--name/--output)")
            return 1
        store.update(args.id, **fields)
        console.print(f"已更新任务 {args.id}")
        return 0

    if cmd == "run":
        job = store.get(args.id)
        if job is None:
            console.print(f"未找到任务 {args.id}")
            return 1
        from ..cron.runner import CronRunner
        runner = CronRunner(kernel, config, os.getcwd(), config.home)
        result = runner.run_job(job, stream=True)
        if result["ok"]:
            console.print(f"任务 {args.id} 执行完成")
        else:
            console.print(f"任务 {args.id} 执行失败: {result['error']}")
        return 0 if result["ok"] else 1

    if cmd == "logs":
        job = store.get(args.id)
        if job is None:
            console.print(f"未找到任务 {args.id}")
            return 1
        sessions_dir = config.home / "sessions"
        if not sessions_dir.exists():
            console.print("没有执行记录")
            return 0
        files = sorted(sessions_dir.glob("*.jsonl"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        shown = 0
        for f in files:
            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if f'"job_id": "{args.id}"' not in content and f'"job_id":"{args.id}"' not in content:
                continue
            shown += 1
            ts = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
            console.print(f"{ts}  {f.name}")
            for line in content.splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = rec.get("type")
                if t == "assistant":
                    console.print(f"  {rec.get('message', {}).get('content', '')[:200]}")
                elif t == "job.error":
                    console.print(f"  错误: {rec.get('error', '')}")
                elif t == "job.done":
                    ok = rec.get("ok")
                    console.print(f"  {'完成' if ok else '失败'}")
            if shown >= 5:
                console.print("…仅显示最近 5 次执行")
                break
        if not shown:
            console.print("该任务还没有执行记录")
        return 0

    if cmd == "tick":
        from ..cron.runner import run_due_jobs
        count = run_due_jobs(kernel, config, os.getcwd(), config.home, stream=True)
        console.print(f"执行了 {count} 个到期任务")
        return 0

    if cmd == "start":
        check = getattr(args, "check", 60)
        if getattr(args, "detach", False):
            if _cron_daemon_alive(config):
                console.print("守护进程已在运行 (PID 文件存在且存活)")
                return 1
            pid = _cron_spawn_daemon(config, os.getcwd(), check)
            _cron_pid_file(config).parent.mkdir(parents=True, exist_ok=True)
            _cron_pid_file(config).write_text(str(pid), encoding="utf-8")
            console.print(f"守护进程已启动 (PID: {pid}, 检查间隔 {check} 秒)")
            return 0
        # 前台运行: 阻塞, Ctrl+C 退出
        from ..cron.daemon import main as daemon_main
        console.print(f"前台运行守护 (检查间隔 {check} 秒), Ctrl+C 退出…")
        return daemon_main(["--check", str(check), "--workspace", os.getcwd(),
                            "--home", str(config.home)])

    if cmd == "stop":
        pid_file = _cron_pid_file(config)
        if not pid_file.exists():
            console.print("守护进程未在运行 (无 PID 文件)")
            return 1
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            console.print("PID 文件损坏, 请手动删除")
            return 1
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, 15)  # SIGTERM 优雅退出
        except OSError as exc:
            console.print(f"进程可能已退出: {exc}")
        pid_file.unlink(missing_ok=True)
        console.print(f"已停止守护进程 (PID: {pid})")
        return 0

    if cmd == "status":
        if _cron_daemon_alive(config):
            pid_str = _cron_pid_file(config).read_text(encoding="utf-8").strip()
            console.print(f"守护进程运行中 (PID: {pid_str})")
        else:
            console.print("守护进程未运行")
        jobs = store.list()
        due = store.due()
        console.print(f"  任务总数: {len(jobs)}, 当前到期: {len(due)}")
        return 0

    console.print("未知 cron 子命令")
    return 1


# ------------------------------------------------------------------ open (精确引用跳转)

def cmd_open(args) -> int:
    """打开文件并定位到行: qxt open <file>[:<line>] (对标 Claude Code 的精确引用跳转)。"""
    target = getattr(args, "target", "")
    if not target:
        console.print("用法: qxt open <file>[:<line>], 例如 qxt open qingxiaotuan/core/agent.py:42")
        return 1
    line = 0
    path_str = target
    if ":" in target:
        path_str, _, line_str = target.rpartition(":")
        try:
            line = int(line_str)
        except ValueError:
            console.print(f"行号无效: {line_str}")
            return 1
    fpath = Path(path_str).expanduser()
    if not fpath.is_absolute():
        fpath = Path.cwd() / fpath
    if not fpath.exists():
        console.print(f"文件不存在: {fpath}")
        return 1
    from ..tools.code import _open_in_editor
    editor = ""
    try:
        kernel = build_kernel()
        editor = kernel.require("config").get("ui.editor", "")
    except Exception as exc:  # noqa: BLE001
        log.debug("读取编辑器配置失败: %s", exc)
    console.print(_open_in_editor(fpath.resolve(), line, editor))
    return 0


# ------------------------------------------------------------------ mcp

def _mcp_build_kernel():
    """构建内核并取出已连接的 MCP 客户端列表 (供 list/tools/call/test 使用)。"""
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
    kernel, clients = _mcp_build_kernel()
    if kernel is None:
        return 1
    cfg = kernel.require("config")
    servers = cfg.get("mcp.servers", []) or []
    if not servers and not clients:
        console.print("未配置任何 MCP server。用 `qxt mcp add <name> <command>` 接入外部工具。")
        return 0
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


def cmd_mcp(args) -> int:
    """MCP 协议管理: 接入 / 浏览 / 调用外部 MCP Server 工具。"""
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
    console.print("未知子命令。可用: list / tools / call / add / test")
    return 1


# ------------------------------------------------------------------ hooks

def cmd_hooks(args) -> int:
    """用户级 Hooks 管理: 列出 / 测试。"""
    sub = getattr(args, "hook_cmd", None)
    workspace = getattr(args, "workspace", None) or os.getcwd()
    if sub == "list":
        return _hooks_list(workspace)
    if sub == "test":
        return _hooks_test(workspace, args)
    console.print("未知子命令。可用: list / test")
    return 1


def _hooks_list(workspace: str) -> int:
    """列出当前配置中生效的 Hooks。"""
    try:
        kernel = build_kernel()
        config = kernel.require("config")
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    mgr = HookManager(config, workspace, kernel=kernel)
    if not mgr.enabled or not mgr.list_hooks():
        console.print("未配置任何 Hooks。在配置中 hooks.<事件> 下添加命令即可 "
                      "(事件: PreToolUse / PostToolUse / SessionStart / SessionEnd)。")
        return 0
    for evt in HOOK_EVENTS:
        specs = [h for h in mgr.list_hooks() if h["event"] == evt]
        if not specs:
            continue
        console.print(f"{evt}")
        for h in specs:
            flag = "阻断" if (h["blocking"] and mgr.allow_blocking) else "审计"
            edit = "可改参" if (h["allow_edit_args"] or mgr.allow_edit_args) else "只读"
            console.print(f"  matcher={h['matcher']!r}  {flag}/{edit}  cmd={' '.join(h['command'])}")
            if h["description"]:
                console.print(f"    {h['description']}")
    return 0


def _hooks_test(workspace: str, args) -> int:
    """触发一次 Hook 事件跑一遍 (连通性测试)。"""
    event = getattr(args, "event", None) or "PreToolUse"
    tool = getattr(args, "tool", None) or "echo_text"
    raw = getattr(args, "tool_input", None) or "{}"
    try:
        payload = json.loads(raw)
    except Exception as exc:
        console.print(f"--json 不是合法 JSON: {exc}")
        return 1
    try:
        kernel = build_kernel()
        config = kernel.require("config")
    except Exception as exc:
        console.print(f"启动失败: {exc}")
        return 1
    mgr = HookManager(config, workspace, kernel=kernel)
    if not mgr.enabled_for(event):
        console.print(f"事件 {event} 未配置任何 hook。")
        return 0
    console.print(f"触发 {event} (tool={tool}) ...")
    if event == "PreToolUse":
        d = mgr.run_pre(tool, payload, dangerous=True)
        console.print(f"  block={d.block}  reason={d.reason!r}  args={d.args!r}")
    elif event == "PostToolUse":
        mgr.run_post(tool, payload, None)
        console.print("  PostToolUse 已触发 (见 hook 脚本输出/审计事件)。")
    else:
        mgr.run_session(event, {"tool": tool, **payload})
        console.print(f"  {event} 已触发。")
    return 0


def _cmd_hooks(agent, arg: str) -> None:
    """斜杠命令 /hooks: 列出当前会话生效的 Hooks。"""
    hooks = getattr(getattr(agent, "ctx", None), "hooks", None)
    if hooks is None or not hooks.enabled:
        ui.info("未启用 Hooks。配置中设置 hooks.enabled=true 并添加 hooks.<事件>。")
        return
    specs = hooks.list_hooks()
    if not specs:
        ui.info("已启用 Hooks, 但当前未配置任何规则。")
        return
    for evt in HOOK_EVENTS:
        rows = [h for h in specs if h["event"] == evt]
        if not rows:
            continue
        ui.info(f"  {evt}:")
        for h in rows:
            flag = "阻断" if (h["blocking"] and hooks.allow_blocking) else "审计"
            edit = "可改参" if (h["allow_edit_args"] or hooks.allow_edit_args) else "只读"
            ui.info(f"    matcher={h['matcher']}  [{flag}/{edit}]  {' '.join(h['command'])}")


# ------------------------------------------------------------------ session

def _format_session_time(mtime: float, now: float) -> str:
    """会话时间: 近期显示相对时间, 更早显示绝对时间。"""
    delta = now - mtime
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)} 天前"
    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def _count_session_messages(path: Path) -> int:
    """统计会话中的消息条数 (user/assistant/tool)。"""
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") in ("user", "assistant", "tool"):
                    n += 1
    except OSError:
        pass
    return n


def _resolve_session_target(files: List[Path], target: str) -> List[Path]:
    """把会话目标 (编号 / 会话ID前缀 / all) 解析为文件列表。"""
    if target == "all":
        return list(files)
    if target.isdigit():
        idx = int(target) - 1
        return [files[idx]] if 0 <= idx < len(files) else []
    return [f for f in files if f.stem.startswith(target)]


def cmd_session(args) -> int:
    """会话管理。"""
    session_cmd = getattr(args, "session_cmd", None)
    sessions_dir = home_dir() / "sessions"
    if not sessions_dir.exists():
        console.print("没有会话记录")
        return 0
    files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if session_cmd == "list":
        from ..memory.sessions import SessionStore
        sessions = files[:10]
        if not sessions:
            console.print("没有会话记录")
            return 0
        table = Table(title="历史会话")
        table.add_column("#", justify="right")
        table.add_column("时间", justify="left")
        table.add_column("标题", justify="left")
        table.add_column("消息", justify="right")
        table.add_column("会话ID", justify="left")
        now = time.time()
        for i, f in enumerate(sessions, 1):
            title = SessionStore.peek_title(f) or "(无标题)"
            table.add_row(
                str(i),
                _format_session_time(f.stat().st_mtime, now),
                title,
                str(_count_session_messages(f)),
                f.stem,
            )
        console.print(table)
        console.print("使用 qxt session resume <编号|会话ID> 恢复会话")
        return 0
    if session_cmd == "resume":
        target = getattr(args, "target", None) or "latest"
        try:
            kernel = build_kernel()
        except Exception as exc:
            console.print(f"启动失败: {exc}")
            return 1
        config = kernel.require("config")
        workspace = getattr(args, "workspace", None) or os.getcwd()
        agent = create_agent(kernel, workspace)
        seed_builtin_skills(kernel)
        loaded = _resume_session(agent, target)
        if loaded is None:
            return 1
        console.print(f"{t('chat.resumed', path=loaded, count=len(agent.messages))}")
        mode = _apply_mode_override(kernel, getattr(args, "mode", None),
                                    yes=getattr(args, "yolo", False))
        effort = config.get("agent.effort", "high")
        return _run_chat_repl(agent, config, workspace, mode, effort)
    if session_cmd == "delete":
        target = getattr(args, "target", None) or "1"
        if target == "latest":
            target = "1"
        if not files:
            console.print("没有会话记录")
            return 0
        targets = _resolve_session_target(files, target)
        if not targets:
            console.print(f"未找到匹配的会话: {target}")
            return 1
        if not getattr(args, "yes", False):
            names = ", ".join(f.stem for f in targets[:5])
            if len(targets) > 5:
                names += f" …(共 {len(targets)} 个)"
            try:
                ans = input(f"  确认删除 {len(targets)} 个会话 ({names})? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans != "y":
                console.print("已取消")
                return 0
        for f in targets:
            try:
                f.unlink()
            except OSError as exc:
                console.print(f"删除失败 {f.name}: {exc}")
                return 1
        console.print(f"已删除 {len(targets)} 个会话")
        return 0
    console.print("session 功能开发中…")
    return 0


# ------------------------------------------------------------------ slash commands

def _cmd_image(agent, head: str, arg: str) -> None:
    """视觉: /image 挂接图片, /images 列出, /clear-images 清除。"""
    if head == "/images":
        if not getattr(agent, "pending_images", None):
            ui.info("当前没有待发送的图片。")
            return
        for i, ref in enumerate(agent.pending_images, 1):
            loc = ref.path or ref.url or "<data>"
            ui.success(f"{i}. {loc} ({ref.byte_size() // 1024}KB, {ref.media_type})")
        return
    if head == "/clear-images":
        n = agent.clear_pending_images()
        ui.info(f"已清除 {n} 张待发送图片。")
        return
    # /image <path|url|data: URI>
    if not arg:
        ui.info("用法: /image <本地路径 | http(s) URL | data: URI>")
        return
    try:
        msg = agent.attach_image(arg.strip())
        ui.success(msg)
    except Exception as exc:  # noqa: BLE001
        ui.error(f"挂接图片失败: {exc}")


def cmd_usercmd(args) -> int:
    """CLI: qxt usercmd [list] — 列出用户自定义斜杠命令。"""
    from ..cli.user_commands import load_user_commands
    workspace = getattr(args, "workspace", None) or os.getcwd()
    try:
        kernel = build_kernel(getattr(args, "profile", "default"))
    except Exception as exc:  # noqa: BLE001
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    table = load_user_commands(config, workspace)
    if not table:
        console.print("没有自定义斜杠命令。")
        console.print("放置 <home>/commands/*.md 或 <workspace>/.qxt/commands/*.md 即可注册 /<命令名>。")
        return 0
    t = Table(title="用户自定义斜杠命令")
    t.add_column("命令", justify="left")
    t.add_column("说明", justify="left")
    t.add_column("参数", justify="left")
    t.add_column("来源", justify="left")
    for uc in sorted(table.values(), key=lambda c: c.name):
        src = "项目级" if str(uc.path).find(os.sep + ".qxt" + os.sep) >= 0 else "用户级"
        t.add_row(f"/{uc.name}", uc.description, uc.argument_hint or "-", src)
    console.print(t)
    console.print("用法: 在会话中输入 /<命令名>  运行 (List 见 qxt usercmd list)")
    return 0


def _cmd_permissions(config) -> None:
    """/permissions — 展示当前生效的权限策略 (只读)。"""
    rules = config.get("permissions.rules", [])
    deny = config.get("permissions.shell.deny_patterns", [])
    net = config.get("permissions.network.allow_domains", [])
    red = config.get("mode.yolo_require_confirm", [])
    ui.info(f"  权限规则 (permissions.rules): {len(rules)} 条")
    for r in rules:
        if isinstance(r, dict):
            p = f" {r.get('pattern')}" if r.get("pattern") else ""
            ui.info(f"    {r.get('action')}  {r.get('tool')}{p}")
    ui.info("  Shell 拒绝规则: " + (", ".join(deny) if deny else "(无)"))
    ui.info("  网络域名白名单: " + (", ".join(net) if net else "(不限制)"))
    ui.info("  YOLO 仍需确认的工具: " + (", ".join(sorted(red)) if red else "(无)"))
    ui.info("  生效说明: 内置加固 > deny > ask > allow > 默认决策 (危险工具默认需确认)。")
    ui.info("  增删规则: qxt config set permissions.rules '[{\"tool\":\"run_shell\",\"action\":\"deny\"}]'")


def _cmd_status(agent, config) -> None:
    """/status — 会话状态摘要 (模型/模式/上下文/用量)。"""
    ui.info(f"  模型: {config.get('model.provider')}/{config.get('model.model')}")
    mode = str(config.mode)
    if agent.yolo:
        mode += " (YOLO)"
    if agent.plan_mode:
        mode += " · PLAN 只读"
    ui.info(f"  模式: {mode}")
    ui.info(f"  推理投入: {config.get('agent.effort', 'high')}")
    ui.info(f"  会话消息: {len(agent.messages)} 条")
    st = agent.context_stats()
    ui.info(f"  上下文: {st.get('estimated_tokens', '?')} / {st.get('budget_tokens', '?')} tokens")
    usage = agent.total_usage
    ui.usage(usage)


def _cmd_budget(agent, config, arg: str) -> None:
    """/budget [USD] — 查看或设置成本预算上限 (router.budget_limit, 0=不限制)。"""
    if arg:
        try:
            amount = float(arg.strip())
        except ValueError:
            ui.error("用法: /budget <USD 金额>  (无参 = 查看当前预算)")
            return
        config.set_user("router.budget_limit", amount)
        ui.success(f"成本预算上限已设: ${amount:.2f}" + ("" if amount > 0 else " (0 = 不限制)"))
        return
    cur = float(config.get("router.budget_limit", 0.0) or 0.0)
    ui.info(f"  成本预算上限: ${cur:.2f}" + (" (到达自动停止)" if cur > 0 else " (不限制)"))
    try:
        spent = agent._estimate_total_cost()
        ui.info(f"  本会话估算已花费: ${spent:.4f}")
    except Exception:  # noqa: BLE001
        pass


def _cmd_checkpoint(agent, arg: str) -> None:
    """/checkpoint [save|list|restore [id]] — 会话检查点。"""
    from ..tools.checkpoint import checkpoint_save, checkpoint_list, checkpoint_restore
    sp = arg.split()
    action = sp[0].lower() if sp else "list"
    if action in ("save", "s"):
        ui.success(checkpoint_save(agent.ctx, " ".join(sp[1:])))
    elif action in ("restore", "r"):
        cid = sp[1] if len(sp) > 1 else ""
        label = cid or "最近一个"
        if agent.ctx.confirm and not agent.ctx.confirm(f"恢复检查点 {label}? 将回滚其后的全部文件变更并把对话截回当时。"):
            ui.info("已取消恢复。")
            return
        ui.success(checkpoint_restore(agent.ctx, cid))
    else:
        ui.info(checkpoint_list(agent.ctx) or "没有已保存的检查点。")


def _cmd_web(agent, arg: str) -> None:
    """/web <URL> 或 /web search <关键词> — 快捷联网抓取 / 搜索。"""
    from ..tools.web import web_search, web_fetch
    if not arg:
        ui.info("用法: /web <URL>  或  /web search <关键词>")
        return
    parts = arg.strip().split(None, 1)
    if parts and parts[0].lower() == "search" and len(parts) > 1:
        ui.answer_md(web_search(agent.ctx, parts[1]))
    else:
        ui.answer_md(web_fetch(agent.ctx, arg.strip()))


def _cmd_subagent(agent, arg: str) -> None:
    """/subagent <目标> — 派发一个隔离子代理 (独立上下文) 执行任务, 只回收摘要。"""
    from ..tools.subagent_tool import _run_subagent
    if not arg:
        ui.info("用法: /subagent <目标> — 派发隔离子代理执行并回收摘要")
        return
    ui.info("[子代理] 已派出, 独立上下文执行中…")
    ui.answer_md(_run_subagent(agent.ctx, arg.strip()))


def _handle_slash(cmd: str, agent, config: Config, workspace: str) -> bool:
    """处理斜杠命令。返回 True 表示已处理。"""
    parts = cmd.strip().split(None, 1)
    head = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if head == "/help":
        ui.info(_HELP)
    elif head == "/tools":
        tools = agent.registry.tools
        for tool in tools:
            ui.info(f"  {tool.name}: {tool.description[:60]}")
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
                # 用户显式切模型 → 本会话暂停自动路由, 尊重其选择
                config.set_user("router.auto_switch", False)
                ui.success("模型已切换 (自动路由本会话已暂停, 尊重你的选择)。")
            elif parts2 and parts2[0] == "auto":
                on = (parts2[1] in ("on", "1", "true", "yes")) if len(parts2) > 1 else True
                config.set_user("router.auto_switch", bool(on))
                ui.success(f"自动路由: {'开启' if on else '关闭'}")
            else:
                ui.info(f"  provider: {config.get('model.provider')}")
                ui.info(f"  model: {config.get('model.model')}")
                ui.info(f"  自动路由: {'开启' if config.get('router.auto_switch', True) else '关闭'}")
        else:
            ui.info(f"  provider: {config.get('model.provider')}")
            ui.info(f"  model: {config.get('model.model')}")
            ui.info(f"  自动路由: {'开启' if config.get('router.auto_switch', True) else '关闭'}")
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
                ui.success(t("chat.resumed", path=name, count=len(agent.messages)))
        else:
            _list_sessions(agent)
    elif head == "/swarm":
        if not arg:
            ui.info("用法: /swarm <目标>  — 多 Agent 协作 (强模型规划 → 弱模型并发执行 → 强模型验收)")
            return True
        from ..core.swarm import Swarm
        ui.info("[多 Agent 协作] 强模型规划中…")
        swarm = Swarm(
            kernel=agent.kernel, config=config, workspace=workspace,
            main_agent=agent, confirm=agent.ctx.confirm,
        )
        collab = swarm.run(arg)
        ui.answer_md(collab.to_report())
        # 把协作结论回灌主 Agent, 便于继续追问细节
        agent.messages.append({
            "role": "user",
            "content": f"(多 Agent 协作已完成, 目标: {arg[:60]})\n{collab.accepted[:1200]}",
        })
    elif head == "/route":
        # 路由: 默认已接通自动切换; 此处展示本回合「会如何路由」
        from ..models.router import ModelRouter
        router = ModelRouter(
            default_provider=config.get("model.provider", "deepseek"),
            default_model=config.get("model.model", ""),
            budget_limit=config.get("router.budget_limit", 0.0),
        )
        auto = bool(config.get("router.enabled", False)) and bool(config.get("router.auto_switch", True))
        if not arg:
            ui.info("  用法: /route <任务描述> — 估算难度并建议/自动路由模型")
            ui.info(f"  自动路由: {'已开启 (本回合按难度自动切换)' if auto else '未开启 (仅咨询建议)'}")
        else:
            d = router.decide(
                arg,
                current_provider=config.get("model.provider"),
                current_model=config.get("model.model"),
                available_providers=ModelRouter.available_provider_names(),
            )
            ui.info(f"  难度评估: {d['difficulty']}/10")
            ui.info(f"  建议模型: {d['provider']}/{d['model']}")
            ui.info(f"  理由: {d['reason']}")
            if auto:
                ui.info(f"  自动路由: {'本回合将切换' if d['switch'] else '本回合保持当前模型'}")
            else:
                ui.info("  自动路由未开启, 仅建议不切换 (用 /model auto on 开启)")
    elif head == "/diff":
        _cmd_diff(workspace, arg)
    elif head == "/undo":
        _cmd_undo(workspace, arg, agent)
    elif head == "/log":
        _cmd_log(agent, arg)
    elif head == "/impact":
        _cmd_impact(workspace, arg, agent)
    elif head == "/mcp":
        # /mcp            -> 列出已配置 server
        # /mcp <server>   -> 列出该 server 的工具
        # /mcp tools [server] 同上 (显式)
        sp = arg.split()
        if sp and sp[0] in ("list", "tools"):
            sub = sp[0]
            server = sp[1] if len(sp) > 1 else None
        else:
            sub = "tools" if sp else "list"
            server = sp[0] if sp else None
        ns = argparse.Namespace(
            mcp_cmd=sub, server=server, tool=None, json=None,
            name=None, command=None, args=[], env=[], timeout=None, workspace=workspace,
        )
        cmd_mcp(ns)
    elif head == "/hooks":
        _cmd_hooks(agent, arg)
    elif head in ("/image", "/images", "/clear-images"):
        _cmd_image(agent, head, arg)
    elif head == "/permissions":
        _cmd_permissions(config)
    elif head == "/status":
        _cmd_status(agent, config)
    elif head == "/budget":
        _cmd_budget(agent, config, arg)
    elif head == "/checkpoint":
        _cmd_checkpoint(agent, arg)
    elif head == "/web":
        _cmd_web(agent, arg)
    elif head == "/subagent":
        _cmd_subagent(agent, arg)
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
  /diff      工作区变更摘要 (支持 /diff <file> / /diff --full)
  /undo      精细回滚 (事务化账本: /undo /undo N /undo <file> /undo all /undo --safe)
  /log       最近工具调用历史 ( /log [N] 默认 15 条 )
  /impact    展示操作账本与影响半径 (哪些文件被改、改了几步)
  /mcp       接入的外部 MCP server 与工具 ( /mcp <server> 看该 server 的工具)
  /image     挂接图片随下一轮发送 ( /image <本地路径|http(s) URL|data: URI> )
  /images    列出当前待发送的图片
  /clear-images  清除待发送的图片
  /hooks     用户级 Hooks ( /hooks 列出已配置脚本; /hooks test 触发一次)
  /model     切换/查看模型
  /effort    切换推理投入 low/medium/high
  /mode      切换运行模式 standard/yolo
  /plan      切换 Plan 模式 (只读分析, 修改类工具被拦截)
  /permissions  展示当前生效的权限策略 (只读)
  /status    会话状态摘要 (模型/模式/上下文/用量)
  /budget    查看/设置成本预算上限 ( /budget <USD> )
  /checkpoint  会话检查点 ( /checkpoint save 保存;  restore [id] 回滚; list 查看 )
  /web       快捷联网 ( /web <URL> 或  /web search <关键词> )
  /subagent  派发隔离子代理 (独立上下文) 执行任务并回收摘要
  /resume    恢复历史会话 (编号/会话id/latest)
  /swarm     多 Agent 协作 (强模型规划 → 弱模型并发执行 → 强模型验收)
  /route     智能模型路由建议 (咨询式, 输入 /route <任务>)
  /clear     清空对话上下文
  /more      展开上一条被折叠的长输出
  /exit      退出

提示: Enter 发送 · Esc+Enter 换行 · 多行可用三引号"""


def cmd_undo(args) -> int:
    """CLI: qxt undo [target] — 跨进程精确回滚 (账本持久化于工作区 .qxt/ledger)。"""
    from ..core.ledger import MutationLedger
    from pathlib import Path

    workspace = str(Path(getattr(args, "workspace", None) or os.getcwd()).resolve())
    kernel = build_kernel(getattr(args, "profile", "default"))
    config = kernel.require("config")
    ledger = MutationLedger(workspace, config)
    n = ledger.load_journal()
    if n == 0:
        ui.info("  没有可回滚的账本记录 (本次/上次会话未修改文件, 或账本为空)。")
        return 0
    os.chdir(workspace)
    target = getattr(args, "target", "") or ""
    if target == "all":
        for d in ledger.undo_all():
            ui.success(d)
        # 危险兜底已移除: 这里曾静默执行 git reset --hard + clean -fd,
        # 会连带丢弃用户未提交的手工修改。账本只回滚账本内记录的工具级变更,
        # git 层面的整理请用户自行执行。
        ui.info("  提示: 账本仅回滚其中记录的工具级变更; git 状态请按需自行执行 git 命令整理。")
        ledger.persist()
        return 0
    if target.isdigit():
        for d in ledger.undo_last(int(target)):
            ui.success(d)
        ledger.persist()
        return 0
    if target:
        res = ledger.undo_file(target)
        if res:
            ui.success(res)
            ledger.persist()
        else:
            ui.error(f"账本中未找到涉及 {target} 的变更。")
        return 0
    for d in ledger.undo_last(1):
        ui.success(d)
    ledger.persist()
    return 0

def _cmd_log(agent, arg: str) -> None:
    """/log [N] — 展示最近 N 条工具调用历史 (默认 15)。"""
    from datetime import datetime
    n = 15
    if arg.strip():
        try:
            n = int(arg.strip())
        except ValueError:
            n = 15
    # 从 session 事件流读取 tool_call 事件
    session = agent.session
    if session is None or not session.file.exists():
        ui.info("  暂无工具调用记录。")
        return
    tool_events = []
    try:
        with open(session.file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json as _json
                    rec = _json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("type") == "tool_call":
                    tool_events.append(rec)
    except Exception:  # noqa: BLE001
        ui.info("  暂无工具调用记录。")
        return
    if not tool_events:
        ui.info("  暂无工具调用记录。")
        return
    recent = tool_events[-n:]
    ui.info("")
    ui.info(f"  === 最近 {len(recent)} 条工具调用 ===")
    ui.info("")
    for ev in recent:
        ts = datetime.fromtimestamp(ev.get("ts", 0)).strftime("%H:%M:%S")
        name = ev.get("name", "?")
        args_str = ev.get("arguments", "")
        # 截断过长的参数
        if len(args_str) > 120:
            args_str = args_str[:117] + "..."
        ui.info(f"    {ts} {name}")
        if args_str:
            ui.info(f"           {args_str}")


def cmd_impact(args) -> int:
    """CLI: qxt impact — 展示操作账本与影响半径 (跨进程读取持久化账本)。"""
    from datetime import datetime
    from pathlib import Path
    from ..core.ledger import MutationLedger

    workspace = str(Path(getattr(args, "workspace", None) or os.getcwd()).resolve())
    kernel = build_kernel(getattr(args, "profile", "default"))
    config = kernel.require("config")
    ledger = MutationLedger(workspace, config)
    ledger.load_journal()
    if ledger.empty():
        ui.info("  操作账本为空: 尚未修改任何文件。")
        return 0
    stats = ledger.stats()
    history = ledger.history()

    # ---- 统计概览
    ui.info("")
    ui.info("  === 影响半径 (Blast Radius) ===")
    ui.info("")
    ui.info(f"  操作总数: {stats['records']}")
    ui.info(f"""  受影响文件: {stats['files_touched']} 个""")

    # ---- 文件分类统计 (按扩展名)
    ext_counts: dict[str, int] = {}
    for f in stats["files"]:
        ext = Path(f).suffix or "(no ext)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    if ext_counts:
        ui.info("")
        ui.info("  文件类型分布:")
        for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
            bar = "#" * min(count, 30)
            ui.info(f"    {ext:12s} {count:3d}  {bar}")

    # ---- 工具使用统计
    tool_counts: dict[str, int] = {}
    for rec in history:
        t = rec["tool"]
        tool_counts[t] = tool_counts.get(t, 0) + 1
    if tool_counts:
        ui.info("")
        ui.info("  工具使用分布:")
        for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            bar = "#" * min(count, 30)
            ui.info(f"    {tool:24s} {count:3d}  {bar}")

    # ---- 时间密度直方图 (按 5 分钟桶)
    if history:
        ui.info("")
        ui.info("  活动时间线 (每 5 分钟)")
        timestamps = [rec["ts"] for rec in history]
        t_min, t_max = min(timestamps), max(timestamps)
        bucket_sec = 300  # 5 分钟
        buckets: dict[int, int] = {}
        for ts in timestamps:
            bid = int((ts - t_min) / bucket_sec)
            buckets[bid] = buckets.get(bid, 0) + 1
        if buckets:
            max_count = max(buckets.values())
            n_buckets = max(buckets.keys()) + 1
            width = 30  # 柱状图最大宽度
            for i in range(n_buckets):
                c = buckets.get(i, 0)
                if c == 0:
                    bar = "_"
                else:
                    bar = "#" * max(1, round(c / max_count * width))
                # 时间标签: 从 t_min 开始
                mins = int((t_min + i * bucket_sec) / 60) % 60
                ui.info(f"    {mins:02d}min  {bar} ({c})")

    # ---- 文件列表
    if stats["files"]:
        ui.info("")
        ui.info("  受影响文件:")
        for f in stats["files"][:30]:
            ui.info(f"    {f}")
        if len(stats["files"]) > 30:
            ui.info(f"    ...还有 {len(stats['files']) - 30} 个")

    # ---- 最近变更时间线
    ui.info("")
    ui.info("  最近变更时间线:")
    for rec in history[-15:]:
        ts = datetime.fromtimestamp(rec["ts"]).strftime("%H:%M:%S")
        targets = ", ".join(rec["targets"][:3])
        if len(rec["targets"]) > 3:
            targets += f" +{len(rec['targets']) - 3}"
        ui.info(f"    [{rec['id']}] {ts} {rec['tool']}: {targets}")

    # ---- 摘要
    ui.info("")
    ui.info(f"  --- {stats['records']} 次操作 / {stats['files_touched']} 个文件 / {len(ext_counts)} 种类型 ---")
    return 0


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


# ------------------------------------------------------------------ /diff & /undo

def _cmd_diff(workspace: str, arg: str) -> None:
    """展示工作区变更摘要。
    /diff           — 摘要 (统计 + 文件列表)
    /diff --stat    — 同上
    /diff <file>    — 指定文件的 diff
    /diff --full    — 完整 diff"""
    import subprocess
    os.chdir(workspace)
    flag = arg.strip().lower()
    if flag and not flag.startswith("--"):
        # 指定文件
        try:
            result = subprocess.run(
                ["git", "diff", "--", flag], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                ui.info(f"  diff -- {flag}:")
                ui.answer_md("```diff\n" + result.stdout.strip()[:4000] + "\n```")
            else:
                ui.info(f"  {flag} 没有变更")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"git diff 失败: {exc}")
        return
    if flag == "--full":
        try:
            result = subprocess.run(
                ["git", "diff"], capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                ui.answer_md("```diff\n" + result.stdout.strip()[:8000] + "\n```")
            else:
                ui.info("  工作区没有变更")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"git diff 失败: {exc}")
        return
    # 摘要模式: 统计 + 文件列表
    try:
        stat = subprocess.run(
            ["git", "diff", "--stat"], capture_output=True, text=True, timeout=5,
        )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--stat"], capture_output=True, text=True, timeout=5,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=5,
        )
        parts = []
        if staged.stdout.strip():
            parts.append(f"[已暂存]\n{staged.stdout.strip()}")
        if stat.stdout.strip():
            parts.append(f"[未暂存]\n{stat.stdout.strip()}")
        if untracked.stdout.strip():
            files = untracked.stdout.strip().splitlines()
            preview = files[:15]
            suffix = f"\n  … 还有 {len(files) - 15} 个" if len(files) > 15 else ""
            parts.append(f"[未追踪] {len(files)} 个文件:\n  " + "\n  ".join(preview) + suffix)
        if parts:
            ui.info("\n".join(parts))
        else:
            ui.info("  工作区干净, 没有变更。")
    except Exception as exc:  # noqa: BLE001
        ui.error(f"git 状态查询失败: {exc}")


def _get_ledger(agent):
    """从 agent 取出事务化操作账本 (可能不存在)。"""
    ctx = getattr(agent, "ctx", None)
    return getattr(ctx, "ledger", None) if ctx is not None else None


def _git_run(args, workspace, timeout: int = 10):
    """在工作区执行 git 子命令, 返回 CompletedProcess。"""
    import subprocess
    return subprocess.run(
        ["git"] + list(args), cwd=workspace, capture_output=True, text=True, timeout=timeout,
    )


def _cmd_undo(workspace: str, arg: str, agent) -> None:
    """精细回滚工作区变更 (事务化账本优先, git 兜底)。

    /undo          — 撤销最近 1 步账本变更 (无账本则 git checkout -- .)
    /undo N        — 撤销最近 N 步账本变更
    /undo <file>   — 撤销最近一条涉及该文件的账本变更
    /undo --safe   — 先 git stash 再回滚 (可 git stash pop 恢复)
    /undo all      — 撤销全部账本变更 + git reset --hard + 清理未追踪"""
    import subprocess
    target = arg.strip()
    ledger = _get_ledger(agent)

    # 安全模式: 先 stash (可恢复)。使用 cwd= 而非全局 os.chdir, 避免污染后续测试的 CWD。
    if target == "--safe":
        try:
            stash_msg = f"qxt-undo-{int(time.time())}"
            result = subprocess.run(
                ["git", "stash", "push", "-m", stash_msg, "--include-untracked"],
                cwd=workspace, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                ui.success(f"已 stash 变更 ({stash_msg})。可用 git stash pop 恢复。")
            else:
                ui.info("  没有可 stash 的变更。")
        except Exception as exc:  # noqa: BLE001
            ui.error(f"stash 失败: {exc}")
        return

    # 账本精细回滚优先 (与 git 无关, 非 git 仓库也能逐文件精确撤销)
    if ledger is not None and not ledger.empty():
        if target == "all":
            confirm = agent.ctx.confirm if agent and agent.ctx.confirm else None
            if confirm and not confirm("将撤销账本记录的全部变更, 不可恢复。确认?"):
                ui.info("  已取消。")
                return
            for d in ledger.undo_all():
                ui.success(d)
            _git_run(["reset", "--hard", "HEAD"], workspace)
            _git_run(["clean", "-fd"], workspace)
            ledger.persist()
            return
        if target.isdigit():
            for d in ledger.undo_last(int(target)):
                ui.success(d)
            ledger.persist()
            return
        if target:
            res = ledger.undo_file(target)
            if res:
                ui.success(res)
                ledger.persist()
            else:
                ui.info(f"  账本中未找到涉及 {target} 的变更, 尝试 git 回滚…")
                r = _git_run(["checkout", "--", target], workspace)
                if r.returncode == 0:
                    ui.success(f"已回滚: {target}")
                else:
                    ui.error(f"回滚失败: {r.stderr.strip() or '未知错误'}")
            return
        for d in ledger.undo_last(1):
            ui.success(d)
        ledger.persist()
        return

    # 无账本记录: 回落到 git
    if target == "all":
        confirm = agent.ctx.confirm if agent and agent.ctx.confirm else None
        if confirm and not confirm("将撤销所有变更 (含未追踪文件), 不可恢复。确认?"):
            ui.info("  已取消。")
            return
        _git_run(["reset", "--hard", "HEAD"], workspace)
        _git_run(["clean", "-fd"], workspace)
        ui.success("已回滚所有变更并清理未追踪文件。")
        return
    if target:
        r = _git_run(["checkout", "--", target], workspace)
        if r.returncode == 0:
            ui.success(f"已回滚: {target}")
        else:
            ui.error(f"回滚失败: {r.stderr.strip() or '未知错误'}")
        return
    r = _git_run(["checkout", "--", "."], workspace)
    if r.returncode == 0:
        ui.success("已回滚所有未暂存的变更。")
    else:
        ui.error(f"回滚失败: {r.stderr.strip() or '未知错误'}")


def _cmd_impact(workspace: str, arg: str, agent) -> None:
    """展示事务化操作账本与当前影响半径 (哪些文件被改、改了几步)。"""
    from datetime import datetime
    from pathlib import Path

    ledger = _get_ledger(agent)
    if ledger is None:
        ui.info("  当前会话未启用操作账本 (ledger.enabled=false)。")
        return
    if ledger.empty():
        ui.info("  操作账本为空: 本次会话尚未修改任何文件。")
        return
    stats = ledger.stats()
    history = ledger.history()

    # ---- 统计概览
    ui.info("")
    ui.info("  === 影响半径 (Blast Radius) ===")
    ui.info("")
    ui.info(f"  操作总数: {stats['records']}")
    ui.info(f"""  受影响文件: {stats['files_touched']} 个""")

    # ---- 文件分类统计 (按扩展名)
    ext_counts: dict[str, int] = {}
    for f in stats["files"]:
        ext = Path(f).suffix or "(no ext)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    if ext_counts:
        ui.info("")
        ui.info("  文件类型分布:")
        for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
            bar = "#" * min(count, 30)
            ui.info(f"    {ext:12s} {count:3d}  {bar}")

    # ---- 工具使用统计
    tool_counts: dict[str, int] = {}
    for rec in history:
        t = rec["tool"]
        tool_counts[t] = tool_counts.get(t, 0) + 1
    if tool_counts:
        ui.info("")
        ui.info("  工具使用分布:")
        for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            bar = "#" * min(count, 30)
            ui.info(f"    {tool:24s} {count:3d}  {bar}")

    # ---- 时间密度直方图 (按 5 分钟桶)
    if history:
        ui.info("")
        ui.info("  活动时间线 (每 5 分钟)")
        timestamps = [rec["ts"] for rec in history]
        t_min, t_max = min(timestamps), max(timestamps)
        bucket_sec = 300  # 5 分钟
        buckets: dict[int, int] = {}
        for ts in timestamps:
            bid = int((ts - t_min) / bucket_sec)
            buckets[bid] = buckets.get(bid, 0) + 1
        if buckets:
            max_count = max(buckets.values())
            n_buckets = max(buckets.keys()) + 1
            width = 30  # 柱状图最大宽度
            for i in range(n_buckets):
                c = buckets.get(i, 0)
                if c == 0:
                    bar = "_"
                else:
                    bar = "#" * max(1, round(c / max_count * width))
                # 时间标签: 从 t_min 开始
                mins = int((t_min + i * bucket_sec) / 60) % 60
                ui.info(f"    {mins:02d}min  {bar} ({c})")

    # ---- 文件列表
    if stats["files"]:
        ui.info("")
        ui.info("  受影响文件:")
        for f in stats["files"][:30]:
            ui.info(f"    {f}")
        if len(stats["files"]) > 30:
            ui.info(f"    ...还有 {len(stats['files']) - 30} 个")

    # ---- 最近变更时间线
    ui.info("")
    ui.info("  最近变更时间线:")
    for rec in history[-15:]:
        ts = datetime.fromtimestamp(rec["ts"]).strftime("%H:%M:%S")
        targets = ", ".join(rec["targets"][:3])
        if len(rec["targets"]) > 3:
            targets += f" +{len(rec['targets']) - 3}"
        ui.info(f"    [{rec['id']}] {ts} {rec['tool']}: {targets}")

    # ---- 摘要
    ui.info("")
    ui.info(f"  --- {stats['records']} 次操作 / {stats['files_touched']} 个文件 / {len(ext_counts)} 种类型 ---")
