"""`qxt cron` —— 定时任务管理 (拆分自 cmd_services.py)。"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, cast

from ..config import Config
from ..cron.store import CronStore
from ..ui.format import Table
from ._ui_singleton import console


def build_kernel(*a, **k):
    """惰性构建内核: 仅实际执行 cron 命令时才加载 app 链。"""
    from ..app import build_kernel as _f
    return _f(*a, **k)


def _cron_pid_file(config) -> Path:
    return cast(Path, config.home / "cron" / "daemon.pid")


def _cron_daemon_alive(config) -> bool:
    """守护进程是否存活。"""
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
    """以独立子进程拉起 cron 守护。"""
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
    """定时任务管理。"""
    config = Config(profile=getattr(args, "profile", "default"),
                    patch_file=getattr(args, "patch", None))
    store = CronStore(config.home)
    cmd = getattr(args, "cron_cmd", None)

    # 仅 run/tick 需要完整内核 (CronRunner / run_due_jobs 依赖完整工具链)
    if cmd in ("run", "tick"):
        try:
            kernel = build_kernel()
        except Exception as exc:
            console.print(f"启动失败: {exc}")
            return 1
        kernel_config = kernel.require("config")
        kernel_store = kernel.get("cron_store")
        if kernel_store is None:
            console.print("定时任务存储未初始化 (cron.enabled=false?)")
            return 1
        if cmd == "run":
            job = kernel_store.get(args.id)
            if job is None:
                console.print(f"未找到任务 {args.id}")
                return 1
            from ..cron.runner import CronRunner
            runner = CronRunner(kernel, kernel_config, os.getcwd(), kernel_config.home)
            result = runner.run_job(job, stream=True)
            if result["ok"]:
                console.print(f"任务 {args.id} 执行完成")
            else:
                console.print(f"任务 {args.id} 执行失败: {result['error']}")
            return 0 if result["ok"] else 1
        from ..cron.runner import run_due_jobs
        count = run_due_jobs(kernel, kernel_config, os.getcwd(), kernel_config.home, stream=True)
        console.print(f"执行了 {count} 个到期任务")
        return 0

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
                os.kill(pid, 15)
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
