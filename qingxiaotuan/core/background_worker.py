"""跨命令后台任务 worker 入口。

命令：`python -m qingxiaotuan.core.background_worker <job_id> <home>`。
worker 从 BackgroundStore 读取任务，状态和心跳持久化到 manifest，日志写入同一用户家目录的会话流。
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from .background_store import BackgroundStore, kill_process_tree
from .agent import Agent
from .markers import is_done
from ..config.loader import Config, load_dotenv
from ..memory.sessions import SessionStore
from ..app import build_kernel, create_agent


def run(job_id: str, home: Path) -> int:
    store = BackgroundStore(home)
    job = store.get(job_id)
    if job is None:
        return 2
    if job.get("status") == "cancel_requested":
        store.update(job_id, status="cancelled")
        return 0
    store.update(job_id, status="running", pid=os.getpid(), heartbeat=time.time())
    session = SessionStore(home)
    store.update(job_id, session_file=str(session.file))
    session.append("session.meta", {"kind": "background", "job_id": job_id,
                                     "task": job["task"], "started_at": job["started_at"]})
    session.append("job.start", {"job_id": job_id, "task": job["task"]})

    def _cancel_and_exit() -> int:
        # 防御性: 清理本进程可能残留的子进程树 (工具调用产生的 shell/node 等)。
        try:
            kill_process_tree(os.getpid())
        except OSError:
            pass
        session.append("job.cancel", {"job_id": job_id})
        turns = agent.turn_count if "agent" in dir() else 0
        store.update(job_id, status="cancelled", heartbeat=time.time(), turns=turns)
        return 0

    try:
        load_dotenv(home)
        kernel = build_kernel(profile=job.get("profile", "default"))
        config = kernel.require("config")
        config.home = home
        confirm = (lambda _prompt: True) if job.get("yolo", False) else (lambda _prompt: False)
        agent = create_agent(kernel, job["workspace"], confirm=confirm)
        agent.session = session
        max_turns = int(config.get("background.max_turns", 50))
        last = ""
        for index in range(1, max_turns + 1):
            current = store.get(job_id)
            if current is None or current.get("status") == "cancel_requested":
                return _cancel_and_exit()
            prompt = job["task"] if index == 1 else (
                f"[继续] 上一轮结果:\n{last[:1500]}\n\n请继续推进任务，直到完成。"
            )
            last = agent.run(prompt, stream=False) or ""
            store.update(job_id, heartbeat=time.time(), turns=agent.turn_count, result=last[:4000])
            session.append("agent.turn", {"turn": agent.turn_count, "summary": last[:500]})
            if last.startswith("[模型错误]"):
                # 模型调用持续失败 (已重试): 快速失败, 不再空转消耗轮次
                error = last[:500]
                store.update(job_id, status="failed", error=error, heartbeat=time.time())
                session.append("job.error", {"job_id": job_id, "error": error})
                return 1
            if is_done(last):
                store.update(job_id, status="done", result=last, heartbeat=time.time())
                session.append("job.done", {"job_id": job_id, "turns": agent.turn_count})
                return 0
        store.update(job_id, status="done", result=last or "(达到轮次上限)", heartbeat=time.time())
        session.append("job.done", {"job_id": job_id, "turns": agent.turn_count, "exhausted": True})
        return 0
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        store.update(job_id, status="failed", error=error, heartbeat=time.time())
        session.append("job.error", {"job_id": job_id, "error": error})
        return 1


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    return run(sys.argv[1], Path(sys.argv[2]))


if __name__ == "__main__":
    raise SystemExit(main())
