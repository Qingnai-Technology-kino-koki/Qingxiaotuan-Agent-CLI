"""斜杠命令处理器 —— REPL 内 /xxx 命令。

拆分自 commands.py。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Optional

from ..i18n import t
from ._ui_singleton import ui
from .cmd_slash_blast import _cmd_blast
from .cmd_slash_goal import _cmd_goal
from .cmd_slash_mcp import _cmd_audit, _cmd_mcp
from .cmd_slash_offline import _cmd_offline
from .cmd_slash_sandbox import _cmd_sandbox




_HELP = """
可用斜杠命令 (Slash Commands)
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
  /mcp       MCP 管理 (list/tools/audit/security)
  /mcp-tools MCP Tool Search 上下文占用 (report/on/off/search)
  /image     挂接图片随下一轮发送 ( /image <本地路径|http(s) URL|data: URI> )
  /images    列出当前待发送的图片
  /clear-images  清除待发送的图片
  /hooks     用户级 Hooks ( /hooks 列出已配置脚本; /hooks test 触发一次)
  /model     切换/查看模型
  /effort    切换推理投入 low/medium/high
  /mode      切换运行模式 standard/yolo
  /plan      切换 Plan 模式 (只读分析, 修改类工具被拦截)
  /goal      Goal 模式 (对标 Claude Code: /goal <目标描述>, 循环直到条件满足)
  /blast     Blast Radius 交互式可视化 (影响半径/安全风险/工具统计)
  /sandbox   沙箱执行 (在 Docker 容器中隔离执行危险命令)
  /offline   离线模式 (Ollama 本地模型管理)
  /audit     审计报告 (统计/查看/导出合规报告)
  /permissions  展示当前生效的权限策略 (只读)
  /status    会话状态摘要 (模型/模式/上下文/用量/韧性)
  /stats     Agent 可观测性面板 (trace/span/延迟/错误率, 需开启 telemetry)
  /budget    查看/设置成本预算上限 ( /budget <USD> )
  /checkpoint  会话检查点 ( /checkpoint save 保存;  restore [id] 回滚; list 查看 )
  /web       快捷联网 ( /web <URL> 或  /web search <关键词> )
  /verify    编码验证闭环 (自动跑 test/typecheck/lint, 失败自修复)
  /subagent  派发隔离子代理 (独立上下文) 执行任务并回收摘要
  /workflow  动态并行工作流管理 ( list/status <wf>/result <wf>/cancel <wf>/retry <wf> )
  /resume    恢复历史会话 (编号/会话id/latest)
  /swarm     多 Agent 协作 (强模型规划 -> 弱模型并发执行 -> 强模型验收)
  /route     智能模型路由建议 (咨询式, 输入 /route <任务>)
  /clear     清空对话上下文
  /more      展开上一条被折叠的长输出
  /exit      退出

提示: Enter 发送 · Esc+Enter 换行 · 多行可用三引号
TUI 中: 输入 / 或 +/ 即会弹出命令补全菜单 (Tab 切换, 回车执行)"""


# 权威命令清单: 与 _handle_slash 的 elif 分支一一对应。
# 供 TUI / REPL 的补全词表、命令发现、ACP/IDE 能力清单共用, 避免多处维护漂移。
SLASH_COMMAND_NAMES: tuple = (
    "/audit", "/blast", "/budget", "/checkpoint", "/clear", "/clear-images", "/compact",
    "/context", "/cost", "/diff", "/effort", "/exit", "/gh-borrow", "/goal",
    "/help", "/hooks", "/image", "/images", "/impact", "/log", "/mcp",
    "/mcp-tools", "/memory", "/mode", "/model", "/more", "/offline", "/permissions",
    "/plan", "/quit", "/resume", "/route", "/sandbox", "/skills", "/stats",
    "/status", "/subagent", "/swarm", "/tools", "/undo", "/usage", "/verify",
    "/web", "/workflow",
)


def list_slash_commands() -> list:
    """枚举支持的斜杠命令名 (供补全词表 / ACP/IDE 能力清单)。"""
    return list(SLASH_COMMAND_NAMES)


def _cmd_image(agent, head: str, arg: str) -> None:
    """/image /images /clear-images: 多模态图片管理。"""
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
    if not arg:
        ui.info("用法: /image <本地路径 | http(s) URL | data: URI>")
        return
    try:
        msg = agent.attach_image(arg.strip())
        ui.success(msg)
    except Exception as exc:  # noqa: BLE001
        ui.error(f"挂接图片失败: {exc}")


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
    """/status — 会话状态摘要。"""
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
    rs = agent.resilience_status()
    ui.info("  韧性:")
    br = rs["circuit_breaker"]
    if br.get("enabled"):
        state = br.get("state", "closed")
        if state == "closed":
            ui.success(f"    熔断: 已启用 · 正常 (closed, 阈值 {br.get('failure_threshold')} / 冷却 {br.get('cooldown')}s)")
        elif state == "half_open":
            ui.info(f"    熔断: 已启用 · 探测中 (half_open, 阈值 {br.get('failure_threshold')} / 冷却 {br.get('cooldown')}s)")
        else:
            ui.error(f"    熔断: 已启用 · 已熔断 (open, 冷却 {br.get('cooldown')}s, 连续失败 {br.get('failures', 0)})")
    else:
        ui.info("    熔断: 未启用 (agent.circuit_breaker.enabled=false)")
    rl = rs["rate_limiter"]
    if rl["active"]:
        conc = f" / 并发<={rl['max_concurrent']}" if rl["max_concurrent"] else ""
        ui.info(f"    限流: 已启用 (<={rl['max_rpm']} req/min{conc})")
    else:
        ui.info("    限流: 未启用 (model.rate_limit.enabled=false)")
    rt = rs["retry"]
    ui.info(f"    重试: <={rt['max_retries']} 次 · 退避 {rt['backoff']}s · 重试图谱 {rt['retry_on']}")


def _cmd_stats(agent, config) -> None:
    """/stats — Agent 可观测性面板。"""
    tc = getattr(agent, "_telemetry", None)
    if tc is None:
        ui.info("可观测性(telemetry)未启用。设置 observability.telemetry.enabled: true 后, "
                "本面板展示 Agent 循环的 trace/span 与延迟/错误率等指标。")
        return
    s = tc.get_stats()
    ui.info("=== Agent 可观测性 (telemetry) ===")
    ui.info(f"  trace/spans: {s['total_traces']}/{s['total_spans']}  ·  活跃 span: {s['active_spans']}")
    ui.info(f"  错误: {s['total_errors']}  ·  错误率: {s['error_rate']}%")
    ui.info(f"  模型调用: 平均 {s['avg_model_latency_ms']} ms  ·  累计 tokens: {s['total_tokens']:,}")
    ui.info(f"  工具调用: {s['total_tool_calls']} 次  ·  平均 {s['avg_tool_latency_ms']} ms")
    if s["recent_model_calls"]:
        last = s["recent_model_calls"][-1]
        ui.info(f"  最近模型调用: {last['latency_ms']:.0f} ms · {last['tokens']} tokens · "
                f"{'成功' if last['success'] else '失败'}")


def _cmd_budget(agent, config, arg: str) -> None:
    """/budget [USD] — 查看或设置成本预算上限。"""
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
    """/web <URL> 或 /web search <关键词> — 快捷联网。"""
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
    """/subagent <目标> — 派发隔离子代理执行任务。"""
    from ..tools.subagent_tool import _run_subagent
    if not arg:
        ui.info("用法: /subagent <目标> — 派发隔离子代理执行并回收摘要")
        return
    ui.info("[子代理] 已派出, 独立上下文执行中…")
    ui.answer_md(_run_subagent(agent.ctx, arg.strip()))


def _cmd_workflow(agent, arg: str) -> None:
    """/workflow — 动态并行工作流管理 (list/status <wf> /result <wf> /cancel <wf> /retry <wf>)。"""
    from ..core.dynamic_workflow import get_engine, close_engines
    from ..config import home_dir

    eng = get_engine(home_dir())
    eng.configure(
        kernel=getattr(agent, "kernel", None),
        config=agent.config if hasattr(agent, "config") else None,
        workspace=getattr(getattr(agent, "ctx", None), "workspace", "") or "",
        main_agent=agent,
    )
    parts = arg.strip().split(None, 1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "list":
        items = eng.list()
        if not items:
            ui.info("(暂无工作流)")
            return
        ui.info(f"工作流共 {len(items)} 个 (最新在前):")
        for it in items[:20]:
            ui.info(f"  {it['workflow_id']:<16} {it['status']:<9} 步骤={it['n_steps']}  {it['name']}")
        return

    if sub in ("status", "result", "cancel", "retry", "retry_failed"):
        wf_id = rest.strip()
        if not wf_id:
            ui.info(f"用法: /workflow {sub} <wf_id>")
            return
        try:
            if sub == "status":
                st = eng.status(wf_id)
                lines = [f"工作流 {st['name']} [{st['workflow_id']}] · 状态={st['status']}"
                         f" · {st['n_steps']} 步 · 并行={st['parallel']}"]
                for s in st["steps"]:
                    lines.append(f"  步骤 {s['step'] + 1}: {s['status']}"
                                 f" ({s['done_tasks']}/{s['n_tasks']})")
                ui.info("\n".join(lines))
            elif sub == "result":
                r = eng.result(wf_id)
                preview = r["summary"][:2000]
                ui.info(f"工作流 {r['name']} [{wf_id}] · 状态={r['status']} · "
                        f"成功 {r['ok_tasks']}/{r['total_done']}")
                ui.answer_md(preview or "(无输出)")
            elif sub == "cancel":
                ok = eng.cancel(wf_id)
                ui.info(f"已请求取消 {wf_id}" if ok else f"工作流 {wf_id} 已结束/正在取消")
            else:
                ok = eng.retry_failed(wf_id)
                ui.info(f"已重置失败任务并重新编排 {wf_id}" if ok
                        else f"{wf_id} 无失败任务可重试")
        except KeyError:
            ui.info(f"找不到工作流 {wf_id}")
        return

    ui.info("用法: /workflow [list|status <wf>|result <wf>|cancel <wf>|retry <wf>]")


def _cmd_verify(workspace: str, arg: str, agent, config) -> None:
    """/verify — 编码验证闭环。"""
    from ..core.verify_loop import (
        VerifyConfig, VerifyReport, verify_once, verify_with_heal,
    )
    cfg = VerifyConfig.from_dict({"verify": config.get("verify", {})})
    mode = "heal"
    if arg.strip().lower() in ("check", "once", "run"):
        mode = "check"
    elif arg.strip().lower() in ("heal", "fix"):
        mode = "heal"
    ui.info(f"  模式: {mode} | 项目类型: {_detect_project_type(workspace)}")
    ui.info("")
    if mode == "check":
        rnd = verify_once(workspace, cfg)
        rnd.round_num = 1
        ui.info(rnd.summary())
    else:
        def _heal_fn(error_text: str) -> str:
            ui.info("  [自修复] 正在分析错误并修复…")
            prompt = (
                f"编码验证失败, 请分析以下错误并修复代码。只修复报错的文件, 不要改其他东西。\n\n"
                f"{error_text}\n\n修复后不要跑测试, 我会自动再验证。"
            )
            result = agent.run(prompt, stream=False)
            return result or ""
        report = verify_with_heal(workspace, cfg, heal_fn=_heal_fn)
        ui.info(report.summary())


def _detect_project_type(workspace: str) -> str:
    from ..core.verify_loop import detect_project_type
    return detect_project_type(workspace)


def _cmd_log(agent, arg: str) -> None:
    """/log [N] — 展示最近 N 条工具调用历史。"""
    from datetime import datetime
    n = 15
    if arg.strip():
        try:
            n = int(arg.strip())
        except ValueError:
            n = 15
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
                    rec = json.loads(line)
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
        if len(args_str) > 120:
            args_str = args_str[:117] + "..."
        ui.info(f"    {ts} {name}")
        if args_str:
            ui.info(f"           {args_str}")


def _cmd_mcp_tools(agent, arg: str) -> None:
    """/mcp-tools — MCP Tool Search 上下文降耗 (报告 / on / off / search)。

    支持:
      /mcp-tools            查看上下文占用报告
      /mcp-tools on|off     强制启用/禁用搜索模式 (运行时热切换)
      /mcp-tools search <q> 在当前 MCP 工具目录里检索并回显命中 schema
    """
    engine = None
    try:
        engine = agent.kernel.get("mcp_tool_search") if agent.kernel else None
    except Exception:  # noqa: BLE001
        engine = None
    if engine is None:
        ui.warn("MCP Tool Search 引擎未启用 (无 MCP 工具接入, 或 mcp.tool_search.enabled=false)")
        return
    head, _, rest = arg.strip().partition(" ")
    if arg and head in ("on", "enable"):
        engine.enabled = True
        ui.success("MCP Tool Search 已强制启用 (下一轮生效)")
        return
    if arg and head in ("off", "disable"):
        engine.enabled = False
        ui.success("MCP Tool Search 已禁用 (下一轮生效)")
        return
    if head == "search":
        q = rest or "list tools"
        hits = engine.search(q)
        if not hits:
            ui.info("未命中任何 MCP 工具。")
            return
        ui.info(f" 关键词 '{q}' 命中 {len(hits)} 个工具:")
        for s in hits:
            fn = s["function"]
            ui.info(f"  - {fn['name']}: {fn.get('description', '')[:70]}")
        return
    rep = engine.context_report()
    ui.info("MCP Tool Search 上下文占用:")
    ui.info(f"  模式: {rep['mode']} (搜索降耗 vs 全量内联)")
    ui.info(f"  已接入 MCP 工具: {rep['mcp_tools']} 个")
    ui.info(f"  MCP schema 占用: {rep['mcp_tokens']:,} tokens 约 {rep['utilization']:.1%}")
    ui.info(f"  上下文窗口: {rep['context_window']:,} · 阈值: {rep['threshold']:.0%}")
    ui.info(f"  累计检索 {rep['search_calls']} 次")
    ui.info("  切换: /mcp-tools on|off · 检索: /mcp-tools search <关键词>")


def _handle_slash(cmd: str, agent, config, workspace: str) -> bool:
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
        ui.success(f"已压缩: 折叠 {dropped} 条历史, {before:,} -> {after:,} tokens")
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
    elif head == "/goal":
        _cmd_goal(agent, arg)
    elif head == "/blast":
        _cmd_blast(agent, arg)
    elif head == "/sandbox":
        _cmd_sandbox(agent, arg)
    elif head == "/offline":
        _cmd_offline(agent, arg)
    elif head == "/audit":
        _cmd_audit(agent, arg)
    elif head == "/resume":
        if arg:
            from .cmd_chat import _resume_session
            name = _resume_session(agent, arg)
            if name:
                ui.success(t("chat.resumed", path=name, count=len(agent.messages)))
        else:
            from .cmd_chat import _list_sessions
            _list_sessions(agent)
    elif head == "/swarm":
        if not arg:
            ui.info("用法: /swarm <目标>  — 多 Agent 协作 (强模型规划 -> 弱模型并发执行 -> 强模型验收)")
            return True
        from ..core.swarm import Swarm
        ui.info("[多 Agent 协作] 强模型规划中…")
        swarm = Swarm(
            kernel=agent.kernel, config=config, workspace=workspace,
            main_agent=agent, confirm=agent.ctx.confirm,
        )
        collab = swarm.run(arg)
        ui.answer_md(collab.to_report())
        agent.messages.append({
            "role": "user",
            "content": f"(多 Agent 协作已完成, 目标: {arg[:60]})\n{collab.accepted[:1200]}",
        })
    elif head == "/route":
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
        from .cmd_agents import _cmd_diff
        _cmd_diff(workspace, arg)
    elif head == "/undo":
        from .cmd_agents import _cmd_undo
        _cmd_undo(workspace, arg, agent)
    elif head == "/log":
        _cmd_log(agent, arg)
    elif head == "/impact":
        from .cmd_agents import _cmd_impact
        _cmd_impact(workspace, arg, agent)
    elif head == "/mcp":
        _cmd_mcp(agent, arg)
    elif head == "/mcp-tools":
        _cmd_mcp_tools(agent, arg)
    elif head == "/hooks":
        from .cmd_services import _cmd_hooks
        _cmd_hooks(agent, arg)
    elif head in ("/image", "/images", "/clear-images"):
        _cmd_image(agent, head, arg)
    elif head == "/permissions":
        _cmd_permissions(config)
    elif head == "/status":
        _cmd_status(agent, config)
    elif head == "/stats":
        _cmd_stats(agent, config)
    elif head == "/budget":
        _cmd_budget(agent, config, arg)
    elif head == "/checkpoint":
        _cmd_checkpoint(agent, arg)
    elif head == "/web":
        _cmd_web(agent, arg)
    elif head == "/subagent":
        _cmd_subagent(agent, arg)
    elif head == "/workflow":
        _cmd_workflow(agent, arg)
    elif head == "/verify":
        _cmd_verify(workspace, arg, agent, config)
    elif head == "/gh-borrow":
        from .cmd_gh import run_borrow_for_slash
        out = run_borrow_for_slash(arg or "", mode="code", limit=5)
        ui.info(out)
    elif head in ("/exit", "/quit"):
        return False
    else:
        ui.info(f"未知命令: {head} (输入 /help 查看可用命令)")
    return True
