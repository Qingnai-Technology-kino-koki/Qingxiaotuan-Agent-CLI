"""Cron 调度执行器 —— 单个任务的执行 + 结果落盘 (会话流), 供 tick 与常驻守护复用。"""

from __future__ import annotations

import logging
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
        try:
            agent = Agent(
                kernel=self.kernel, config=self.config, workspace=self.workspace,
                confirm=lambda _p: False,  # 定时任务无人值守, 默认拒绝危险操作
            )
            out = agent.run(job.get("prompt", ""), stream=stream) or ""
            session.append("assistant", {"role": "assistant", "content": out[:4000]})
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            session.append("job.error", {"error": err})
            log.warning("cron 任务 %s 失败: %s", job.get("id"), err)
        if store is not None:
            store.mark_run(job["id"])
        session.append("job.done", {"job_id": job["id"], "ok": err is None,
                                    "session_file": str(session.file)})
        return {"ok": err is None, "output": out, "error": err,
                "session_file": str(session.file)}


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
    runner = CronRunner(kernel, config, workspace, home)
    done = 0
    for job in due:
        result = runner.run_job(job, stream=stream)
        done += 1
        if on_job:
            on_job(job, result)
    return done


# 延迟导入避免循环引用 (CronStore 在 store.py)
from .store import CronStore  # noqa: E402
