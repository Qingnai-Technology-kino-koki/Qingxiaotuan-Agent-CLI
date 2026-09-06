"""任务执行 + 事务化回滚/影响分析 CLI 命令。

拆分自 commands.py:
- cmd_dev: 自主开发循环
- cmd_run: headless 一次性任务
- cmd_agent: 后台自主任务
- cmd_bg: 后台任务管理
- cmd_undo: 跨进程精确回滚
- cmd_impact: 操作账本影响半径展示
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from ..config import Config
from ..logging_conf import log
from ..ui.format import Table

# Lazy import to avoid circular dependency with commands.py
# The ui object is accessed via module attribute to support test mocking
from ._ui_singleton import ui, console


# ---- app 链惰性加载: 仅真正执行 dev/run/agent/bg/undo/impact 时才构建内核 ----
def build_kernel(*a, **k):
    from ..app import build_kernel as _f
    return _f(*a, **k)


def create_agent(*a, **k):
    from ..app import create_agent as _f
    return _f(*a, **k)


def seed_builtin_skills(*a, **k):
    from ..app import seed_builtin_skills as _f
    return _f(*a, **k)




# ===================================================================== cmd_dev

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
    from .cmd_chat import _apply_mode_override
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

    from ..core.devloop import DevLoop
    loop = DevLoop(agent, config, on_checkpoint=on_checkpoint)
    console.print(f"开始自主开发: {task}")
    out = loop.run(task, stream=config.get("model.stream", True))
    console.print(f"\n完成: {out[:500]}")
    return 0


# ===================================================================== cmd_run

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
    from .cmd_chat import (_apply_mode_override, resolve_mode_from_permission,
                           _run_json_schema, _run_session_hooks, _setup_security_alerts)
    # 显式 --mode 优先, 否则 fallback 到 --permission-mode 映射 (对齐 cmd_chat)
    eff_mode = getattr(args, "mode", None) or resolve_mode_from_permission(args)
    mode = _apply_mode_override(kernel, eff_mode, yes=getattr(args, "yes", False))
    if getattr(args, "effort", None):
        config.set_user("agent.effort", args.effort)
    if getattr(args, "model", None):
        config.set_user("model.model", args.model)
    workspace = getattr(args, "workspace", None) or os.getcwd()
    agent = create_agent(kernel, workspace)
    # 会话级 allowedTools (--allowed-tools): 命中者免确认, 仅本会话生效
    at = getattr(args, "allowed_tools", None)
    if at:
        from ..core.allowed_tools import AllowedTools
        agent.ctx.allowed_tools = AllowedTools.parse(at)
    # Plan 模式: 窗口 Agent 只读 (工具分发层硬拒写操作)
    if mode == "plan":
        agent.plan_mode = True
        agent.ctx.plan_mode = True
    _setup_security_alerts()
    _run_session_hooks(agent, "SessionStart", {
        "workspace": workspace,
        "model": config.get("model.model"),
        "provider": config.get("model.provider"),
    })
    seed_builtin_skills(kernel)

    max_cost = getattr(args, "max_cost", 0.0)
    if max_cost > 0:
        config.set_user("router.budget_limit", max_cost)
    max_turns = getattr(args, "max_turns", 0) or 0
    session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex()

    if getattr(args, "bg", False):
        from ..core.background import submit_background
        job_id = submit_background(kernel, agent, task, workspace)
        console.print(f"已提交后台任务: {job_id}")
        return 0

    def on_token(t: str) -> None:
        sys.stdout.write(t)
        sys.stdout.flush()

    json_schema_spec = getattr(args, "json_schema", None)
    if json_schema_spec:
        return _run_json_schema(agent, task, json_schema_spec, args, session_id)

    answer = agent.run(
        task,
        stream=not getattr(args, "no_stream", False),
        on_token=on_token,
        session_id=session_id,
        max_iterations=max_turns if max_turns > 0 else None,
    )
    if answer:
        console.print(f"\n{answer}")
    if max_cost > 0:
        spent = agent._estimate_total_cost()
        print(f"\n[预算] 上限 ${max_cost:.2f} | 已用 ${spent:.4f} | 剩余 ${max_cost - spent:.4f}")
    return 0


# ===================================================================== cmd_agent

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
    from .cmd_chat import _apply_mode_override
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


# ===================================================================== cmd_bg

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
    elif bg_cmd == "shell":
        _cli_bg_shell(args)
    return 0


def _cli_bg_shell(args) -> None:
    """qxt bg shell <action> [job_id] — 管理用 `!` 启动的后台 shell 任务。"""
    from ..core.background_shell import BackgroundShellManager
    manager = BackgroundShellManager()
    action = getattr(args, "action", "list")
    job_id = getattr(args, "job_id", "") or ""
    tail = getattr(args, "tail", 20)
    offset = getattr(args, "offset", 0)
    timeout = getattr(args, "timeout", 0)
    if action == "list":
        view = manager.status_dict()
        if not view.get("ok"):
            console.print(f"[错误] {view.get('error')}")
            return
        console.print(f"后台 shell 任务共 {view['total']} 个 (按状态: {view['by_status']}):")
        for j in view["jobs"]:
            console.print(
                f"  {j['job_id']:<14} {j['status']:<9} "
                f"returncode={j.get('returncode')} elapsed={j['elapsed']}s  {j['command'][:60]}")
        return
    if not job_id:
        console.print("提示: bg shell status|logs|wait|cancel 需提供 job_id (先 bg shell list)")
        return
    if action == "status":
        view = manager.status_dict(job_id)
        if not view.get("ok"):
            console.print(f"[错误] {view.get('error')}")
            return
        console.print(f"job={view['job_id']} status={view['status']} "
                      f"returncode={view.get('returncode')} elapsed={view['elapsed']}s "
                      f"pid={view.get('pid')}\n命令: {view['command']}")
    elif action == "logs":
        if offset > 0:
            view = manager.tail_since(job_id, offset=offset, limit=tail)
            if not view.get("ok"):
                console.print(f"[错误] {view.get('error')}"); return
            console.print(f"(offset={offset} 之后新增, next_offset={view['next_offset']})")
            console.print(view["text"] or "(暂无新输出)")
        else:
            console.print(manager.tail(job_id, tail))
    elif action == "wait":
        try:
            job = manager.wait(job_id, timeout=timeout or None)
        except KeyError:
            console.print(f"[错误] 后台 shell 任务 {job_id} 未找到")
            return
        console.print(f"status={job.status} returncode={job.returncode}")
        console.print(job.tail(tail))
    elif action == "cancel":
        ok = manager.cancel(job_id)
        console.print(f"[已终止] {job_id}" if ok else f"[错误] job={job_id} 不存在或已结束")
    elif action == "prune":
        removed = manager.prune_disk()
        manager.prune_finished()
        console.print(f"[已清理] 移除了 {removed} 个已结束后台任务的磁盘占位")
    else:
        console.print(f"[错误] 未知操作: {action}")


# ===================================================================== cmd_undo / cmd_impact

def cmd_undo(args) -> int:
    """CLI: qxt undo [target] — 跨进程精确回滚。"""
    from ..core.ledger import MutationLedger
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


def cmd_impact(args) -> int:
    """CLI: qxt impact — 展示操作账本与影响半径。"""
    from datetime import datetime
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

    ui.info("")
    ui.info("  === 影响半径 (Blast Radius) ===")
    ui.info("")
    ui.info(f"  操作总数: {stats['records']}")
    ui.info(f"  受影响文件: {stats['files_touched']} 个")

    # 文件分类统计
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

    # 工具使用统计
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

    # 时间密度直方图
    if history:
        ui.info("")
        ui.info("  活动时间线 (每 5 分钟)")
        timestamps = [rec["ts"] for rec in history]
        t_min = min(timestamps)
        bucket_sec = 300
        buckets: dict[int, int] = {}
        for ts in timestamps:
            bid = int((ts - t_min) / bucket_sec)
            buckets[bid] = buckets.get(bid, 0) + 1
        if buckets:
            max_count = max(buckets.values())
            n_buckets = max(buckets.keys()) + 1
            width = 30
            for i in range(n_buckets):
                c = buckets.get(i, 0)
                bar = "_" if c == 0 else "#" * max(1, round(c / max_count * width))
                mins = int((t_min + i * bucket_sec) / 60) % 60
                ui.info(f"    {mins:02d}min  {bar} ({c})")

    if stats["files"]:
        ui.info("")
        ui.info("  受影响文件:")
        for f in stats["files"][:30]:
            ui.info(f"    {f}")
        if len(stats["files"]) > 30:
            ui.info(f"    ...还有 {len(stats['files']) - 30} 个")

    ui.info("")
    ui.info("  最近变更时间线:")
    for rec in history[-15:]:
        ts = datetime.fromtimestamp(rec["ts"]).strftime("%H:%M:%S")
        targets = ", ".join(rec["targets"][:3])
        if len(rec["targets"]) > 3:
            targets += f" +{len(rec['targets']) - 3}"
        ui.info(f"    [{rec['id']}] {ts} {rec['tool']}: {targets}")

    ui.info("")
    ui.info(f"  --- {stats['records']} 次操作 / {stats['files_touched']} 个文件 / {len(ext_counts)} 种类型 ---")
    return 0


# ===================================================================== /diff & /undo 内部实现

def _get_ledger(agent):
    """从 agent 取出事务化操作账本。"""
    ctx = getattr(agent, "ctx", None)
    return getattr(ctx, "ledger", None) if ctx is not None else None


def _git_run(args, workspace, timeout: int = 10):
    """在工作区执行 git 子命令。"""
    return subprocess.run(
        ["git"] + list(args), cwd=workspace, capture_output=True, text=True, timeout=timeout,
    )


def _cmd_diff(workspace: str, arg: str) -> None:
    """展示工作区变更摘要。"""
    os.chdir(workspace)
    flag = arg.strip().lower()
    if flag and not flag.startswith("--"):
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


def _cmd_undo(workspace: str, arg: str, agent) -> None:
    """精细回滚工作区变更 (事务化账本优先, git 兜底)。"""
    import subprocess
    target = arg.strip()
    ledger = _get_ledger(agent)

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
    """展示事务化操作账本与当前影响半径。"""
    from datetime import datetime
    ledger = _get_ledger(agent)
    if ledger is None:
        ui.info("  当前会话未启用操作账本 (ledger.enabled=false)。")
        return
    if ledger.empty():
        ui.info("  操作账本为空: 本次会话尚未修改任何文件。")
        return
    stats = ledger.stats()
    history = ledger.history()

    ui.info("")
    ui.info("  === 影响半径 (Blast Radius) ===")
    ui.info("")
    ui.info(f"  操作总数: {stats['records']}")
    ui.info(f"  受影响文件: {stats['files_touched']} 个")

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

    if history:
        ui.info("")
        ui.info("  活动时间线 (每 5 分钟)")
        timestamps = [rec["ts"] for rec in history]
        t_min = min(timestamps)
        bucket_sec = 300
        buckets: dict[int, int] = {}
        for ts in timestamps:
            bid = int((ts - t_min) / bucket_sec)
            buckets[bid] = buckets.get(bid, 0) + 1
        if buckets:
            max_count = max(buckets.values())
            n_buckets = max(buckets.keys()) + 1
            width = 30
            for i in range(n_buckets):
                c = buckets.get(i, 0)
                bar = "_" if c == 0 else "#" * max(1, round(c / max_count * width))
                mins = int((t_min + i * bucket_sec) / 60) % 60
                ui.info(f"    {mins:02d}min  {bar} ({c})")

    if stats["files"]:
        ui.info("")
        ui.info("  受影响文件:")
        for f in stats["files"][:30]:
            ui.info(f"    {f}")
        if len(stats["files"]) > 30:
            ui.info(f"    ...还有 {len(stats['files']) - 30} 个")

    ui.info("")
    ui.info("  最近变更时间线:")
    for rec in history[-15:]:
        ts = datetime.fromtimestamp(rec["ts"]).strftime("%H:%M:%S")
        targets = ", ".join(rec["targets"][:3])
        if len(rec["targets"]) > 3:
            targets += f" +{len(rec['targets']) - 3}"
        ui.info(f"    [{rec['id']}] {ts} {rec['tool']}: {targets}")

    ui.info("")
    ui.info(f"  --- {stats['records']} 次操作 / {stats['files_touched']} 个文件 / {len(ext_counts)} 种类型 ---")
