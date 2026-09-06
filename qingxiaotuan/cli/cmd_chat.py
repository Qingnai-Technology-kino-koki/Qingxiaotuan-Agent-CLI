"""交互式对话 + 模型配置 + 配置管理 CLI 命令。

拆分自 commands.py (原 3105 行), 负责:
- cmd_chat: 交互式对话 (REPL / TUI / --print)
- cmd_mode: 查看/切换运行模式
- cmd_model: 配置/热切换模型供应商
- cmd_config: 配置管理
- cmd_doctor: 环境健康检查
- 相关辅助函数
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from .. import __version__
from ..config import Config, home_dir
from ..models.provider_catalog import (
    PROVIDER_CATEGORIES, get_provider,
)
from ..logging_conf import log
from ..i18n import ensure_language, t
from ..ui.plain_console import console
from ._ui_singleton import ui
from .cmd_config import _validate_config
from .cmd_slash import list_slash_commands


# ---- app 链惰性加载: 构建内核 (~1s) 只在真正进入交互/需要内核时才付出 ----
def build_kernel(*a, **k):
    from ..app import build_kernel as _f
    return _f(*a, **k)


def create_agent(*a, **k):
    from ..app import create_agent as _f
    return _f(*a, **k)


def seed_builtin_skills(*a, **k):
    from ..app import seed_builtin_skills as _f
    return _f(*a, **k)


def _run_json_schema(agent, task_text: str, json_schema_spec: str,
                     args, session_id: str) -> int:
    """--json-schema 结构化输出: 注入指令 -> 跑任务 -> 校验 -> 失败修复重试。

    返回进程退出码 (0=校验通过; 1=任务/校验失败)。
    """
    from ..core.json_schema import (
        build_instruction, load_schema, run_structured,
    )
    import json as _json

    schema, err = load_schema(json_schema_spec)
    if err:
        print(f"[错误] json-schema: {err}", file=sys.stderr)
        return 1
    strict = bool(getattr(args, "json_schema_strict", True))
    retries = max(0, int(getattr(args, "json_schema_retries", 3)))

    # 结构化输出指令注入会话: 追加为 system 消息, 紧随内置系统提示
    instruction = build_instruction(schema, strict=strict)
    if agent.messages and agent.messages[0].get("role") == "system":
        agent.messages.insert(1, {"role": "system", "content": instruction})
    else:
        agent.messages.insert(0, {"role": "system", "content": instruction})

    def _executor(task: str) -> str:
        try:
            return agent.run(task, stream=False, session_id=session_id) or ""
        except Exception as exc:  # noqa: BLE001
            return f"[错误] {exc}"

    result = run_structured(_executor, schema, max_attempts=retries + 1,
                            strict=strict, initial_task=task_text)
    if not result.ok:
        print(_json.dumps({
            "ok": False, "error": result.error, "attempts": result.attempts,
            "raw": result.raw,
        }, ensure_ascii=False, indent=2))
        return 1

    print(_json.dumps(result.value, ensure_ascii=False, indent=2))
    return 0





# ===================================================================== 会话辅助

def _load_session_into_agent(agent, path: Path) -> str:
    """把会话文件的消息重建进 agent.messages (补回系统提示)。返回会话文件名。"""
    from ..memory.sessions import SessionStore
    messages = SessionStore.load_messages(path)
    agent.messages = [{"role": "system", "content": agent._system_prompt}] + messages
    return path.name


def _resume_session(agent, arg: str) -> Optional[str]:
    """从历史会话恢复上下文 (对标 Claude Code --continue/--resume)。"""
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
    import datetime as _dt
    store = agent.session
    if store is None:
        console.print("会话存储未初始化")
        return
    from ..memory.sessions import SessionStore
    sessions = store.list_sessions()[:10]
    if not sessions:
        console.print("没有历史会话")
        return
    for i, p in enumerate(sessions, 1):
        title = SessionStore.peek_title(p)
        ts = _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
        console.print(f"  {i:2d}. {p.stem}  {ts}  {title}")
    console.print("输入 /resume <编号|会话id|latest> 恢复")


# ===================================================================== 首次运行

def _auto_first_run() -> bool:
    """首次运行检测: 若家目录不存在或无 API Key, 自动引导快速设置。"""
    from .cmd_setup import cmd_setup  # 局部导入, 避免循环依赖
    home = home_dir()
    if (home / "config.yaml").exists():
        return False
    console.print("首次使用青小团? 让我们快速设置一下…")
    return cmd_setup(type("Args", (), {"quick": True})()) == 0


# ===================================================================== cmd_chat

def _prepare_agent(args, workspace: str, session_id: str, on_progress=None):
    """构建内核 + Agent + 会话钩子 + 内置技能 (TUI / REPL / --print 共用)。

    返回 (kernel, config, agent, mode, effort); 会话恢复失败时返回 None。
    异常由调用方处理 (TUI 走加载屏错误态, REPL/print 直接报错)。
    on_progress(pct, status) 可选, 用于 TUI 加载屏进度反馈。
    """
    if on_progress:
        on_progress(30, "构建内核…")
    kernel = build_kernel()
    config = kernel.require("config")
    ensure_language(config)
    # 显式 --mode 优先, 否则 fallback 到 --permission-mode 映射
    eff_mode = getattr(args, "mode", None) or resolve_mode_from_permission(args)
    mode = _apply_mode_override(kernel, eff_mode,
                                yes=getattr(args, "yolo", False))
    if getattr(args, "effort", None):
        config.set_user("agent.effort", args.effort)
    if on_progress:
        on_progress(55, "创建 Agent…")
    agent = create_agent(kernel, workspace)
    # 会话级 allowedTools (--allowedTools): 命中者免确认; 仅本会话生效
    at = getattr(args, "allowed_tools", None)
    if at:
        from ..core.allowed_tools import AllowedTools
        agent.ctx.allowed_tools = AllowedTools.parse(at)
    # Plan 模式启动: 窗口 Agent 只读 (工具分发层硬拒写操作, 见 tools/base.py:230)
    plan_requested = bool(getattr(args, "plan", False)) or (mode == "plan")
    if plan_requested:
        agent.plan_mode = True
        agent.ctx.plan_mode = True
    _setup_security_alerts()
    _run_session_hooks(agent, "SessionStart", {
        "workspace": workspace,
        "model": config.get("model.model"),
        "provider": config.get("model.provider"),
    })
    if on_progress:
        on_progress(80, "注册会话/技能…")
    seed_builtin_skills(kernel)
    resume_arg = getattr(args, "resume", None)
    if resume_arg:
        loaded = _resume_session(agent, resume_arg)
        if loaded is None:
            return None
    if on_progress:
        on_progress(95, "就绪…")
    return kernel, config, agent, mode, config.get("agent.effort", "high")


def cmd_chat(args) -> int:
    """交互式对话 (默认命令)。"""
    _auto_first_run()
    workspace = getattr(args, "workspace", None) or os.getcwd()
    # A4 事件溯源: 为本次会话(REPL/TUI/print 整体)生成一个独立可回放的 session_id
    session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    # --print 非交互模式 (headless, 同步构建)
    is_print = getattr(args, "print_mode", False)
    if is_print:
        prep = _prepare_agent(args, workspace, session_id)
        if prep is None:
            return 1
        kernel, config, agent, mode, effort = prep
        task_text = getattr(args, "task", "") or ""
        if not task_text and not sys.stdin.isatty():
            task_text = sys.stdin.read().strip()
        if not task_text:
            print("[错误] --print 模式需要提供任务描述 (qxt --print chat \"任务\") 或通过 stdin 输入")
            return 1
        max_cost = getattr(args, "max_cost", 0.0)
        if max_cost > 0:
            config.set_user("router.budget_limit", max_cost)
        max_turns = getattr(args, "max_turns", 0)
        if max_turns > 0:
            config.set_user("agent.max_iterations", max_turns)
        def _print_token(t: str) -> None:
            sys.stdout.write(t)
            sys.stdout.flush()

        json_schema_spec = getattr(args, "json_schema", None)
        if json_schema_spec:
            return _run_json_schema(agent, task_text, json_schema_spec, args, session_id)

        answer = agent.run(
            task_text, stream=not getattr(args, "no_stream", False),
            on_token=_print_token, session_id=session_id,
        )
        if answer:
            print(answer)
        usage = agent.total_usage
        prompt_t = usage.get("prompt_tokens", 0)
        compl_t = usage.get("completion_tokens", 0)
        cache_hit = usage.get("prompt_cache_hit_tokens", 0)
        cost = agent._estimate_total_cost()
        cost_str = f" | cost=${cost:.4f}" if cost else ""
        print(f"\n--- tokens: {prompt_t}+{compl_t}, cache_hit={cache_hit}{cost_str} ---", file=sys.stderr)
        return 0

    # 全屏 TUI 模式 (青小团 kimi-fusion: 单 Application, 加载屏内嵌, 无双屏切换崩溃)
    if getattr(args, "tui", False):
        from ..tui.tui import QxtTUI
        state = {"agent": None, "kernel": None, "config": None, "mode": "standard"}

        def submit_in_tui(text: str) -> Optional[str]:
            agent = state["agent"]
            if agent is None:
                return t("tui.booting_wait")
            def on_tool(name: str, arguments: str) -> None:
                tui.begin_tool(name)
            def on_tool_result(name: str, result: str) -> None:
                tui.end_tool(name, ok=True)
            def on_token(delta: str) -> None:
                tui.stream_assistant(delta)
            def on_reason(delta: str) -> None:
                tui.stream_reason(delta)
            def on_error(message: str) -> None:
                tui.append_log(f"[错误] {message}")
                tui.audit("warning", f"[错误] {message}")
                tui.end_any_tool()
            answer = agent.run(
                text,
                stream=True,
                on_token=on_token,
                on_reason=on_reason,
                on_tool=on_tool,
                on_tool_result=on_tool_result,
                on_error=on_error,
                session_id=session_id,
            )
            tui.end_stream()
            # 每轮结束后用真实上下文用量刷新占用条; 只更新已用量、保持分母为模型
            # 上下文窗口, 避免分母在"模型窗口/配置预算"间切换导致的百分比跳变。
            try:
                st = agent.context_stats()
                if st.get("budget_tokens"):
                    tui.set_context_used(st["estimated_tokens"])
            except Exception:
                pass
            return answer or t("tui.no_output")

        def command_in_tui(text: str) -> Optional[str]:
            agent = state["agent"]
            if text.strip() in ("/exit", "/quit"):
                tui.close()
                return t("tui.exiting")
            if agent is None:
                return t("tui.booting_wait")
            from .slash import _handle_slash
            handled = _handle_slash(text, agent, state["config"], workspace)
            tui.set_plan_mode(agent.plan_mode)
            return t("tui.cmd_done") if handled else t("tui.exiting")

        tui = QxtTUI(submit_in_tui, title="青小团", workspace=workspace,
                     on_cancel=lambda: state["agent"].cancel() if state["agent"] else None,
                     on_command=command_in_tui,
                     commands=list_slash_commands())
        tui.set_boot_progress(5, "正在启动青小团…")

        def _boot() -> None:
            try:
                prep = _prepare_agent(args, workspace, session_id,
                                      on_progress=tui.set_boot_progress)
                if prep is None:
                    tui.boot_error(t("tui.resume_fail"))
                    return
                kernel, config, agent, mode, effort = prep
                state.update(agent=agent, kernel=kernel, config=config, mode=mode)
                # 把内核已注册的内置工具名喂给 TUI 侧栏 TOOLS 标签 (防御式: 取不到则跳过)
                try:
                    _reg = kernel.require("tool_registry")
                    _names = [t.name for t in _reg.tools]
                except Exception:
                    _names = []
                tui.set_ready(config, agent, workspace, tools=_names,
                              no_key=not config.api_key(), mode=mode)
            except Exception as exc:
                import traceback as _tb
                _tb.print_exc()
                tui.boot_error(f"{type(exc).__name__}: {exc}")

        threading.Thread(target=_boot, name="qxt-boot", daemon=True).start()
        try:
            tui.run()
        except BaseException as exc:
            import traceback as _tb2
            _tb_text = _tb2.format_exc()
            _tb2.print_exc()
            # 把崩溃 traceback 落到日志文件, 方便在特殊终端下复现排查
            try:
                _crash_log = os.path.join(
                    os.path.expanduser("~"), ".qingxiaotuan", "tui_crash.log")
                os.makedirs(os.path.dirname(_crash_log), exist_ok=True)
                with open(_crash_log, "w", encoding="utf-8") as _cf:
                    _cf.write(_tb_text)
                console.print(
                    f"[青小团] TUI 运行异常, 已回退到普通 REPL。\n"
                    f"          错误详情已保存到: {_crash_log}\n"
                    f"          请把该文件内容发我, 以便精准修复。")
            except Exception:
                console.print(
                    f"[青小团] TUI 运行异常, 已回退到普通 REPL: {exc}")
            # 兜底: 即便花哨 TUI 在特殊终端下崩溃, 也绝不静默闪退, 给用户可用的界面
            if state["agent"] is not None and state["config"] is not None:
                try:
                    _run_chat_repl(state["agent"], state["config"], workspace,
                                  state.get("mode", "standard"),
                                  state["config"].get("agent.effort", "high"),
                                  session_id=session_id)
                except Exception:
                    pass
        finally:
            if state["agent"] is not None:
                _run_session_hooks(state["agent"], "SessionEnd", {"workspace": workspace})
        return 0

    # 普通 REPL 模式
    prep = _prepare_agent(args, workspace, session_id)
    if prep is None:
        return 1
    kernel, config, agent, mode, effort = prep
    try:
        rc = _run_chat_repl(agent, config, workspace, mode, effort, session_id=session_id)
    finally:
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


def _security_alerter(event) -> None:
    """安全事件 -> 桌面实时告警 (吞异常, 绝不干扰主流程)。"""
    try:
        from ..ext.notify_engine import NotifyEngine
        eng = NotifyEngine()
        etype = str(event.event_type).split(".")[-1]
        cmd = (event.payload.get("command") or "")[:80]
        reason = event.payload.get("reason") or etype
        msg = f"{reason}: {cmd}" if cmd else reason
        eng.notify({"title": "青小团 · 安全事件", "message": msg})
    except Exception:  # noqa: BLE001
        pass


def _setup_security_alerts() -> None:
    """为安全事件总线注册桌面实时告警 (仅交互式会话启用, 测试不受影响)。

    默认把审计事件持久化到 ~/.qingxiaotuan/security-audit.jsonl, 这样拦截 / 放行
    等关键安全事件落盘可追溯 (qxt safe status 可看到落盘路径)。
    """
    try:
        from ..config.loader import home_dir
        from ..core.security_bus import get_security_bus
        get_security_bus(persist_path=home_dir() / "security-audit.jsonl").set_alerter(
            _security_alerter
        )
    except Exception:  # noqa: BLE001
        pass


def _run_chat_repl(agent, config: Config, workspace: str, mode: str, effort: str,
                   session_id: Optional[str] = None) -> int:
    """普通 REPL 对话循环 (cmd_chat 与 session resume 共用)。"""
    ui.banner(config, workspace, f"{config.get('model.provider')}/{config.get('model.model')}",
              mode=mode, effort=effort)
    # 安全多阶段确认的交互入口 (令牌 + 屏幕位置变化)
    agent.ctx.confirm = ui.confirm
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
            from .slash import _handle_slash
            _handle_slash(user_input.strip(), agent, config, workspace)
            ui.status_bar(mode, effort, workspace, plan=agent.plan_mode)
            continue
        _run_turn(agent, user_input, config, session_id=session_id)
        ui.status_bar(mode, effort, workspace, plan=agent.plan_mode)

    return 0


def _run_turn(agent, user_input: str, config: Config, stream: bool | None = None,
              session_id: Optional[str] = None):
    """执行一轮对话 (模型 → 工具 → 观察 → 回答)。"""

    def on_tool(name: str, arguments: str) -> None:
        ui.tool_call(name, _parse_args(arguments))

    answer = agent.run(
        user_input,
        stream=stream if stream is not None else config.get("model.stream", True),
        on_token=lambda t: ui.stream(t),
        on_tool=on_tool,
        on_reason=lambda r: ui.reason(r),
        on_tool_result=lambda n, r: ui.tool_result(n, r),
        on_error=lambda m: ui.error(m),
        session_id=session_id,
    )
    if answer:
        if config.get("ui.markdown", True):
            ui.answer_md(answer)
        else:
            ui.info(answer)
    else:
        console.print()
    ui.add_tokens(agent.total_usage.get("prompt_tokens", 0) + agent.total_usage.get("completion_tokens", 0))
    st = agent.context_stats()
    if st.get("budget_tokens"):
        ui.set_context_pct(st["estimated_tokens"] / st["budget_tokens"] * 100,
                           used=st["estimated_tokens"], budget=st["budget_tokens"])
    if config.get("ui.show_token_usage", True):
        ui.usage(agent.total_usage)
    console.print()


def _parse_args(a):
    try:
        return json.loads(a)
    except Exception:
        return a


# ===================================================================== cmd_mode

def cmd_mode(args) -> int:
    """查看/切换默认运行模式。"""
    config = Config(profile=getattr(args, "profile", "default"),
                    patch_file=getattr(args, "patch", None))
    value = getattr(args, "value", None)
    if value:
        config.set_user("mode.default", value)
        console.print(f"默认模式已切换为: {value}")
    else:
        current = config.get("mode.default", "standard")
        console.print(f"  当前默认模式: {current}")
    return 0


# ===================================================================== cmd_model

def _show_provider_models(name: str) -> None:
    """打印某供应商的可选模型清单。"""
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
    """剥离模型清单项的 |free/|paid 标注。"""
    return m.split("|")[0] if "|" in m else m


def _pick_api_key_interactive(preset) -> str:
    """提示用户输入 API Key。"""
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
    """展示供应商模型清单并让用户选择。"""
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
    """跨供应商搜索模型。"""
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
    """跨供应商搜索模型, 选择后写入配置。"""
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
        console.print("编号超出范围")
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
    """只读配置视图: 在基础 config 上覆盖若干键 (不写盘)。"""

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
    """连通性测试。"""
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
        console.print("未配置 API Key — 无法测试。请先运行 qxt models 或 qxt setup。")
        return 1
    console.print(f"  端点: {base_url or '(未设置)'}")
    console.print(f"  模型: {provider}/{model}")
    try:
        from ..models import create_adapter
        adapter = create_adapter(config)
        t0 = time.time()
        resp = adapter.chat([{"role": "user", "content": "ping"}], stream=False)
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
        console.print(f"  token: 入 {usage.get('prompt_tokens', 0)} / 出 {usage.get('completion_tokens', 0)}")
    return 0


def _model_local() -> int:
    """列出本机已安装的本地模型 (Ollama + llama.cpp)。"""
    try:
        from ..models.offline import detect_local_models
        status = detect_local_models()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[错误] 本地模型探测失败: {exc}")
        return 1

    ollama = status.get("ollama", {})
    llama = status.get("llamacpp", {})

    console.print("\n[bold]本机本地模型[/bold]\n")

    if ollama.get("available"):
        models = ollama.get("models") or []
        console.print(f"[green]✓ Ollama 可用[/green] (已安装 {len(models)} 个)")
        for m in models:
            console.print(f"    • {m}")
        if not models:
            console.print("    运行 `ollama pull <model>` 下载, 例如 qwen2.5:7b")
    else:
        console.print("[dim]• Ollama 未检测到[/dim]")
        if ollama.get("error"):
            console.print(f"    {ollama['error']}")

    console.print("")
    if llama.get("available"):
        models = llama.get("models") or []
        console.print(f"[green]✓ llama.cpp 可用[/green] (:8080/v1, 已加载 {len(models)} 个)")
        for m in models:
            console.print(f"    • {m}")
        if not models:
            console.print("    用 llama.cpp 服务器加载模型后此处会列出")
    else:
        console.print("[dim]• llama.cpp 未检测到 (:8080/v1)[/dim]")
        if llama.get("error"):
            console.print(f"    {llama['error']}")

    console.print("\n[bold]快速接入[/bold]")
    console.print("    qxt models set ollama <模型名>      # 例如 qxt models set ollama qwen2.5:7b")
    console.print("    qxt models set llamacpp <模型名>    # 例如 qxt models set llamacpp local-model")
    console.print("    qxt models test --provider ollama    # 验证连通性")
    return 0


def cmd_model(args) -> int:
    """配置/热切换模型供应商。"""
    model_cmd = getattr(args, "model_cmd", None)
    config = Config(profile=getattr(args, "profile", "default"),
                    patch_file=getattr(args, "patch", None))

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
            console.print("  API Key: 未配置 — 调用模型会失败")
        console.print(f"  temperature: {config.get('model.temperature', 0.7)}")
        console.print(f"  max_tokens:  {config.get('model.max_tokens', 8192)}")
        console.print("  切换: qxt models 或 qxt models set <provider> <model>")
    elif model_cmd == "local":
        return _model_local()
    elif model_cmd == "test":
        return _model_test(config, args)
    elif model_cmd == "set":
        provider = getattr(args, "provider", None)
        model_name = getattr(args, "model", None)
        base_url = getattr(args, "base_url", None)
        api_key_env = getattr(args, "api_key_env", None)
        if provider:
            config.set_user("model.provider", provider)
        if model_name:
            config.set_user("model.model", model_name)
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
        kernel = build_kernel()
        ModelPlugin.switch_model(kernel, overrides)
        config.set_user("router.auto_switch", False)
        console.print("模型已热切换 (当前会话生效, 自动路由已暂停)。")
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
        # 无子命令: 交互式选择
        console.print("选择模型供应商")
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


# ===================================================================== cmd_doctor

def cmd_doctor(args) -> int:
    """环境健康检查: 配置 / 依赖 / 引擎 / 网络 / 模型连通 / 工作区, 分级输出。"""
    issues: List[tuple] = []

    def mark(level: str) -> str:
        return {"ok": "✓", "warn": "!", "err": "✗"}[level]

    console.print("环境检查")
    console.print(f"  Python: {sys.version.split()[0]} ({sys.executable})")
    console.print(f"  版本: {__version__}")

    # ---- 依赖版本检查 ----
    console.print("\n依赖")
    dep_checks = [
        ("openai", "openai", "OpenAI 兼容传输层 (可选)"),
        ("yaml", "pyyaml", "YAML 配置解析"),
        ("rich", "rich", "终端 UI 渲染"),
        ("httpx", "httpx", "HTTP 客户端 (模型适配器)") ,
        ("prompt_toolkit", "prompt_toolkit", "交互式输入"),
    ]
    for mod_name, pkg_name, desc in dep_checks:
        try:
            mod = __import__(mod_name)
            ver = getattr(mod, "__version__", "?")
            console.print(f"  {mark('ok')} {pkg_name} {ver} — {desc}")
        except ImportError:
            # openai 为可选传输层 (OpenAI 兼容适配器惰性导入), 缺失仅提示不报错;
            # rich/httpx/prompt_toolkit 为核心 UI/HTTP 依赖, 缺失同样降级为警告而非致命错误。
            optional = pkg_name in ("rich", "httpx", "prompt_toolkit", "openai")
            level = "warn" if optional else "err"
            console.print(f"  {mark(level)} {pkg_name} 未安装 — {desc}" + (" (可选)" if optional else ""))
            if not optional:
                issues.append((level, pkg_name, f"未安装: {desc}"))

    try:
        kernel = build_kernel()
    except Exception as exc:  # noqa: BLE001
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")

    # ---- 配置健康 ----
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

    # ---- 网络连通 (urllib.request 替代 httpx, 零外部依赖) ----
    console.print("\n网络")
    base_url = config.get("model.base_url", "") or ""
    if not base_url:
        preset = get_provider(config.get("model.provider", ""))
        base_url = (preset.base_url if preset else "") or ""
    if base_url:
        host = base_url.split("//")[-1].split("/")[0]
        try:
            import urllib.request
            import ssl
            t0 = time.time()
            req = urllib.request.Request(base_url, method="HEAD")
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
                resp.read()
            console.print(f"  {mark('ok')} {host} 可达 ({time.time() - t0:.0f}ms)")
        except Exception as exc:  # noqa: BLE001
            console.print(f"  {mark('warn')} {host} 不可达: {exc}")
            issues.append(("warn", "network", f"{host} 不可达"))
    else:
        console.print(f"  {mark('warn')} 未设置 base_url, 跳过网络检查")
        issues.append(("warn", "network", "未设置 base_url"))

    # ---- 模型连通 ----
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

    # ---- 磁盘空间 ----
    console.print("\n磁盘")
    try:
        import shutil
        usage = shutil.disk_usage(home_dir())
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        pct = (usage.free / usage.total) * 100
        level = "ok" if pct > 10 else ("warn" if pct > 5 else "err")
        console.print(f"  {mark(level)} {free_gb:.1f}GB 可用 / {total_gb:.1f}GB 总计 ({pct:.0f}%)")
        if pct <= 5:
            issues.append((level, "disk", f"磁盘空间不足: {free_gb:.1f}GB"))
    except Exception:  # noqa: BLE001
        console.print(f"  {mark('warn')} 无法检测磁盘空间")

    # ---- 汇总 ----
    errs = [i for i in issues if i[0] == "err"]
    warns = [i for i in issues if i[0] == "warn"]
    if errs:
        console.print(f"\n发现 {len(errs)} 个错误, {len(warns)} 个警告")
        return 1
    if warns:
        console.print(f"\n发现 {len(warns)} 个警告")
        return 0
    console.print("\n全部正常 ✓")
    return 0


# ===================================================================== 模式覆盖辅助

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


_PERMISSION_MODE_MAP = {
    # Claude Code 权限模式 -> 青小团内部模式
    "default": "standard",
    "acceptEdits": "standard",
    "plan": "plan",
    "auto": "standard",          # 自动模式分类器未内置, 退化为 standard
    "dontAsk": "yolo",
    "bypassPermissions": "yolo",
}


def resolve_mode_from_permission(args) -> Optional[str]:
    """根据 --permission-mode 推导内部模式; 显式 --mode 优先。返回 None 表示无需覆盖。"""
    pm = getattr(args, "permission_mode", None)
    if pm:
        return _PERMISSION_MODE_MAP.get(pm, "standard")
    return None
