"""Cron 调度执行器 —— 单个任务的执行 + 结果落盘 (会话流), 供 tick 与常驻守护复用。"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..core.agent import Agent
from ..memory.sessions import SessionStore

log = logging.getLogger("qingxiaotuan.cron")


class CronRunner:
    """执行 cron 任务并把结果写进独立会话流 (审计/回放)。

    设计:
    - run_job 是无副作用的: 不修改调用方状态, 只读写 store + 写会话流;
    - 任何任务异常都被捕获, 标记为失败, 绝不炸调度主循环;
    - tick 与常驻守护共用同一个 run_job, 行为一致。
    """

    def __init__(self, kernel, config, workspace: str, home: Path) -> None:
        self.kernel = kernel
        self.config = config
        self.workspace = workspace
        self.home = home

    def run_job(self, job: Dict[str, Any], *, stream: bool = False) -> Dict[str, Any]:
        """执行单个 cron 任务, 返回 {ok, output, error} 并把结果写进会话流。"""
        store: Optional["CronStore"] = self.kernel.get("cron_store")
        max_out = int(self.config.get("cron.max_output_chars", 4000)) if self.config else 4000
        session = SessionStore(self.home)
        session.append("session.meta", {
            "kind": "cron",
            "job_id": job["id"],
            "name": job.get("name"),
            "prompt": job.get("prompt"),
            "started_at": time.time(),
        })
        session.append("user", {"role": "user", "content": job.get("prompt", "")})
        out: str = ""
        err: Optional[str] = None
        # 无人值守任务必须有墙钟预算: 单个任务卡死不能拖垮调度循环
        # (否则守护心跳停摆, 后面所有到期任务全部饿死)。到点强判失败,
        # 工作线程留作 daemon 线程, 由进程退出回收。
        timeout = float(self.config.get("cron.job_timeout", 600)) if self.config else 600.0
        holder: Dict[str, str] = {}

        def _target() -> None:
            try:
                agent = Agent(
                    kernel=self.kernel, config=self.config, workspace=self.workspace,
                    confirm=lambda _p: False,  # 定时任务无人值守, 默认拒绝危险操作
                )
                holder["out"] = agent.run(job.get("prompt", ""), stream=stream) or ""
            except Exception as exc:  # noqa: BLE001
                holder["err"] = f"{type(exc).__name__}: {exc}"

        worker = threading.Thread(target=_target, name=f"cron-{job.get('id', '?')}", daemon=True)
        worker.start()
        worker.join(timeout=timeout)
        if worker.is_alive():
            err = f"执行超时 (>{timeout:.0f}s), 已放弃本次执行"
            log.warning("cron 任务 %s 超时", job.get("id"))
        else:
            out = holder.get("out", "")
            err = holder.get("err") or None
        if err is None:
            session.append("assistant", {"role": "assistant", "content": out[:max_out]})
        else:
            session.append("job.error", {"error": err})
            log.warning("cron 任务 %s 失败: %s", job.get("id"), err)
        if store is not None:
            store.mark_run(job["id"])
        session.append("job.done", {"job_id": job["id"], "ok": err is None,
                                    "session_file": str(session.file)})
        _write_output(job, out, err)
        if self.config is not None and self.config.get("cron.notify", True):
            # 桌面通知是 fire-and-forget 的 UX 糖: Windows 上 toast 失败会回退
            # 气球通知 (PowerShell 脚本内含 Start-Sleep), 同步发送可拖住调度
            # 循环十几秒 —— 扔到 daemon 线程异步发, 调度不等它。
            threading.Thread(
                target=_notify, args=(job, err), daemon=True,
                name=f"cron-notify-{job.get('id', '?')}",
            ).start()
        return {"ok": err is None, "output": out, "error": err,
                "session_file": str(session.file)}


def _os_lock_nb(handle) -> bool:
    """跨平台非阻塞独占加锁 (Windows: msvcrt; POSIX: fcntl)。成功返回 True。"""
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # POSIX 专属模块, Windows 类型桩缺失
            handle.seek(0)
            fcntl.flock(handle.fileno(), getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"))  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def _os_unlock(handle) -> None:
    """释放 _os_lock_nb 加的锁 (失败静默: 进程退出也会释放)。"""
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # POSIX 专属模块, Windows 类型桩缺失
            fcntl.flock(handle.fileno(), getattr(fcntl, "LOCK_UN"))  # type: ignore[attr-defined]
    except OSError:
        pass


@contextlib.contextmanager
def _sweep_lock(home: Path):
    """调度互斥锁: 防止守护与手动 tick (或多个 tick) 并发重复执行同一批任务。

    - Windows 用 msvcrt.locking, POSIX 用 fcntl.flock, 均为非阻塞独占;
    - 抢不到锁说明另一进程正在执行本轮调度, 调用方应整体跳过 (不排队不阻塞);
    - 锁随文件句柄存活, 进程崩溃/退出即释放, 不留死锁。
    """
    lock_dir = Path(home) / "cron"
    lock_dir.mkdir(parents=True, exist_ok=True)
    handle = open(lock_dir / "scheduler.lock", "a+")
    try:
        yield _os_lock_nb(handle)
    finally:
        _os_unlock(handle)
        handle.close()


def run_due_jobs(
    kernel,
    config,
    workspace: str,
    home: Path,
    *,
    stream: bool = False,
    on_job: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None,
) -> int:
    """执行所有到期任务, 返回执行数量。tick 与守护共用。"""
    store = kernel.get("cron_store")
    if store is None:
        return 0
    due = store.due()
    if not due:
        return 0
    # 无到期任务不碰锁文件; 有到期任务时先抢互斥锁, 抢不到说明另一进程在跑
    with _sweep_lock(home) as acquired:
        if not acquired:
            log.info("另一调度进程正在执行, 本轮跳过 (%d 个到期任务)", len(due))
            return 0
        runner = CronRunner(kernel, config, workspace, home)
        done = 0
        for job in due:
            result = runner.run_job(job, stream=stream)
            done += 1
            if on_job:
                on_job(job, result)
        return done


def _write_output(job: Dict[str, Any], out: str, err: Optional[str]) -> None:
    """把任务结果写入 job.output 指定的文件 (供监控/CI 消费), 失败静默。"""
    target = job.get("output")
    if not target:
        return
    try:
        path = Path(target).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        header = f"# {job.get('name', job.get('id', 'cron'))} @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        body = f"[失败] {err}\n" if err else (out or "(无输出)")
        path.write_text(header + body + "\n", encoding="utf-8")
    except OSError:
        pass


def _notify(job: Dict[str, Any], err: Optional[str]) -> None:
    """任务执行完成后发送桌面通知 (失败/成功都提示, 失败带原因)。"""
    try:
        from ..ext.notify_engine import NotifyEngine
        name = job.get("name") or job.get("id", "?")
        if err:
            title, message = f"⏰ 定时任务失败: {name}", err[:120]
        else:
            title, message = f"⏰ 定时任务完成: {name}", "已执行完毕, 结果见会话流"
        NotifyEngine().notify({"title": title, "message": message})
    except Exception:  # noqa: BLE001
        pass


# 延迟导入避免循环引用 (CronStore 在 store.py)
from .store import CronStore  # noqa: E402
