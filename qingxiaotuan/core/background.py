"""后台自主运行 ("手") —— 让青小团在终端之外自己干活。

场景:
- `qxt agent "把本周的日志整理成周报"` —— 终端立刻返回, 任务在后台循环推进;
- `qxt run "任务" --bg` —— headless 任务也支持后台, 不阻塞调用方 (适合 CI / 脚本)。

实现:
- 在独立线程里跑 Agent.run, 复用全部既有工具 (shell / web_fetch / filesystem / code),
  这些工具就是青小团的"手";
- 进度写入**独立的后台会话流** (SessionStore), 主端可用 `qxt bg list / logs` 查询;
- 受 background.max_turns 限制, 防止失控;
- 不阻塞当前终端; 多任务可并行 (每个后台任务一个线程 + 一份会话流)。

线程安全: 模型适配器多为网络 IO 绑定, 并发调用由各自 Agent 持有; 工具注册表是
只读共享 (dispatch 内部), 子 Agent 与主 Agent 同享内核, 由 SubAgentPool 负责隔离。
"""

from __future__ import annotations

import logging
import json
import threading
import time
import uuid
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .kernel import Kernel
from .agent import Agent
from .markers import is_done
from ..memory.sessions import SessionStore
from .background_store import BackgroundStore, _is_process_alive

log = logging.getLogger(__name__)


@dataclass
class BackgroundJob:
    job_id: str
    task: str
    started_at: float
    thread: Optional[threading.Thread]
    store: SessionStore
    status: str = "running"          # running | done | failed | cancelled
    result: str = ""
    error: Optional[str] = None
    turns: int = 0
    _final_event: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "task": self.task,
            "status": self.status,
            "started_at": self.started_at,
            "elapsed": round(time.time() - self.started_at, 1),
            "turns": self.turns,
            "session_file": str(self.store.file),
            "error": self.error,
        }

    def tail(self, n: int = 15) -> List[str]:
        """最近 n 条会话流的文本摘要 (供 bg logs 查看进展)。"""
        try:
            lines = self.store.file.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            log.debug("读取后台会话流失败 (%s): %s", self.store.file, exc)
            return []
        out: List[str] = []
        for ln in lines[-n * 3:]:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            t = rec.get("type")
            if t == "user":
                out.append(f"[你] {rec.get('message', {}).get('content', '')[:120]}")
            elif t == "assistant":
                c = rec.get("message", {}).get("content", "")
                if c:
                    out.append(f"[青小团] {c[:200]}")
            elif t == "tool_call":
                out.append(f"  ⎿ {rec.get('name')} {str(rec.get('arguments'))[:80]}")
        return out[-n:]


class BackgroundRunner:
    """管理一组后台自主任务 (青小团的"手"在后台同时干活)。"""

    def __init__(self, kernel: Kernel, config, workspace: str) -> None:
        self.kernel = kernel
        self.config = config
        self.workspace = workspace
        self._jobs: Dict[str, BackgroundJob] = {}
        self._lock = threading.Lock()
        self._enabled = bool(config.get("background.enabled", True))
        self._store = BackgroundStore(config.home)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def submit(
        self,
        task: str,
        confirm: Optional[Callable[[str], bool]] = None,
        stream: bool = False,
        on_progress: Optional[Callable[[str, str], None]] = None,
        exclude_tools: tuple = (),
        system_extra: str = "",
    ) -> BackgroundJob:
        """提交一个后台任务, 立即返回 job 句柄 (不阻塞)。

        exclude_tools / system_extra 供 task 工具的后台模式透传
        子代理类型的能力边界与角色指令 (默认空, 向后兼容)。
        """
        if not self._enabled:
            raise RuntimeError("后台模式已在配置中关闭 (background.enabled=false)")
        job_id = "bg-" + uuid.uuid4().hex[:8]
        store = SessionStore(self.config.home)  # 独立会话流
        # 自描述元数据: 让 `qxt session list` 能识别这是后台任务并按任务标题展示,
        # 也让 `qxt session resume` 能断点续聊同一份后台会话流。
        store.append("session.meta", {
            "kind": "background",
            "job_id": job_id,
            "task": task,
            "started_at": time.time(),
        })
        store.append("job.start", {"job_id": job_id, "task": task})

        agent = Agent(
            kernel=self.kernel,
            config=self.config,
            workspace=self.workspace,
            confirm=confirm,
            exclude_tools=exclude_tools,
            system_extra=system_extra,
        )
        # 让 Agent 的进度写入后台会话流 (而非主会话)
        agent.session = store

        max_turns = int(self.config.get("background.max_turns", 50))
        report_every = int(self.config.get("background.report_every", 5))

        job = BackgroundJob(
            job_id=job_id, task=task, started_at=time.time(),
            thread=None, store=store,
        )

        def _work() -> None:
            try:
                # 多轮: 每轮跑一次 Agent.run, 直到模型判定完成或达到 max_turns
                last = ""
                for i in range(1, max_turns + 1):
                    if job.status == "cancelled":
                        return
                    prompt = task if i == 1 else (
                        f"[继续] 上一轮结果:\n{last[:1500]}\n\n请继续推进任务, "
                        f"直到完成。若已完成, 直接给最终交付总结。"
                    )
                    out = agent.run(
                        prompt, stream=stream,
                        on_token=(lambda t: on_progress(job_id, t)) if on_progress else None,
                        on_tool=(lambda n, a: store.append("tool_call", {"name": n, "arguments": a[:300]})) if on_progress else None,
                    )
                    last = out or ""
                    job.turns = agent.turn_count
                    store.append("agent.turn", {"turn": agent.turn_count, "summary": last[:500]})
                    if i % report_every == 0 and on_progress:
                        on_progress(job_id, f"[进展] 第 {i} 轮完成, 累计 {agent.turn_count} 轮对话")
                    # 简单完成判定: 模型明确收尾 (与 DevLoop 共用同一标记集合)
                    if is_done(last):
                        job.status = "done"
                        job.result = last
                        store.append("job.done", {"job_id": job_id, "turns": agent.turn_count})
                        return
                job.status = "done"
                job.result = last or "(达到轮次上限)"
                store.append("job.done", {"job_id": job_id, "turns": agent.turn_count, "exhausted": True})
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                store.append("job.error", {"job_id": job_id, "error": job.error})

        t = threading.Thread(target=_work, name=f"bg-{job_id}", daemon=True)
        job.thread = t
        t.start()
        with self._lock:
            self._jobs[job_id] = job
        return job

    def submit_detached(self, task: str, *, yolo: bool = False) -> Dict[str, Any]:
        """启动独立 worker，返回持久化任务信息。

        worker 不依赖当前 CLI 进程生命周期；后续 CLI 进程通过 manifest 查询同一任务。
        """
        if not self._enabled:
            raise RuntimeError("后台模式已在配置中关闭 (background.enabled=false)")
        job_id = "bg-" + uuid.uuid4().hex[:8]
        data = self._store.create(job_id, task, self.workspace,
                                   getattr(self.config, "profile", "default"))
        data["yolo"] = yolo
        self._store.write(data)
        env = dict(os.environ)
        env["QXT_HOME"] = str(self.config.home)
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        process = subprocess.Popen(
            [sys.executable, "-m", "qingxiaotuan.core.background_worker", job_id, str(self.config.home)],
            cwd=self.workspace, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        updated = self._store.update(job_id, pid=process.pid, status="running", heartbeat=time.time())
        # 校验 worker 真的起来了: Popen 成功只代表 OS 创建了进程, 不代表它能跑完
        # import (例如 background_worker 依赖缺失会瞬间退出)。若不校验, manifest 会停
        # 在 running 而进程早已死, 任务永远跑不完, CLI 还以为后台在干活。
        deadline = time.time() + 3.0
        current: Optional[Dict[str, Any]] = None
        while time.time() < deadline:
            if not _is_process_alive(process.pid):
                current = self._store.get(job_id)
                reason = (current or {}).get("error") if current else None
                self._store.update(job_id, status="failed",
                                   error=reason or "worker 进程未能存活 (subprocess 已退出)")
                raise RuntimeError(f"后台 worker 启动失败: {reason or '进程已退出'}")
            current = self._store.get(job_id)
            if current and current.get("status") == "running" and current.get("pid") == process.pid:
                break
            time.sleep(0.1)
        return updated or current or {}

    def list_jobs(self) -> List[BackgroundJob]:
        self._store.reconcile_stale(float(self.config.get("background.heartbeat_timeout", 90)))
        jobs: List[BackgroundJob] = []
        for data in self._store.list():
            home = Path(getattr(self.config, "home", None) or self.workspace)
            store = SessionStore(home)
            # session_file 显式存为 None 时回退到默认路径 (dict.get 不替 None 兜底)。
            session_file = data.get("session_file") or (home / "sessions" / f"{data['job_id']}.jsonl")
            store.file = Path(session_file)
            jobs.append(BackgroundJob(
                job_id=data["job_id"], task=data.get("task", ""),
                started_at=data.get("started_at", time.time()), thread=None, store=store,
                status=data.get("status", "unknown"), result=data.get("result", ""),
                error=data.get("error"), turns=int(data.get("turns", 0)),
            ))
        with self._lock:
            for job in self._jobs.values():
                if not any(item.job_id == job.job_id for item in jobs):
                    jobs.append(job)
        return sorted(jobs, key=lambda job: job.started_at, reverse=True)

    def get(self, job_id: str) -> Optional[BackgroundJob]:
        self._store.reconcile_stale(float(self.config.get("background.heartbeat_timeout", 90)))
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        data = self._store.get(job_id)
        if data is None:
            return None
        # config.home 可能缺失 (如测试/异常配置), 用 workspace 兜底, 避免 getter 崩溃。
        _ch = getattr(self.config, "home", None)
        _ws = getattr(self, "workspace", None)
        home = Path(str(_ch if _ch is not None else _ws))
        session = SessionStore(home)
        # session_file 在 manifest 中显式存为 None 时, dict.get 会返回 None 而非默认路径,
        # 故用 `or` 兜底 (None / 空串都回退到默认会话文件)。
        session_file = data.get("session_file") or (home / "sessions" / f"{job_id}.jsonl")
        session.file = Path(session_file)
        return BackgroundJob(job_id=job_id, task=data.get("task", ""),
                             started_at=data.get("started_at", time.time()), thread=None,
                             store=session, status=data.get("status", "unknown"),
                             result=data.get("result", ""), error=data.get("error"),
                             turns=int(data.get("turns", 0)))

    def cancel(self, job_id: str) -> bool:
        """取消一个后台任务, 真正终止其 worker 进程/线程及其子进程树。

        返回 True 表示已发出取消 (进程树已杀 / 线程已标记退出); False 表示任务
        不存在或已不在运行。
        """
        job = self.get(job_id)
        if job is None or job.status not in ("running", "queued"):
            return False

        # 1) detached worker: 从 manifest 取到真实 pid, 杀掉整个进程组 (连根拔起)。
        manifest = self._store.get(job_id)
        pid = manifest.get("pid") if manifest else None
        if pid:
            from .background_store import kill_process_tree
            kill_process_tree(int(pid))

        # 2) 线程版任务: 置 cancelled, _work 循环每轮开头会立即退出。
        with self._lock:
            live = self._jobs.get(job_id)
        if live is not None:
            live.status = "cancelled"

        # 3) 落盘最终状态。
        self._store.update(job_id, status="cancelled", heartbeat=time.time())
        if live is not None:
            live.store.append("job.cancel", {"job_id": job_id})
        return True

    def wait(self, job_id: str, timeout: Optional[float] = None) -> Optional[BackgroundJob]:
        """阻塞等待某后台任务结束 (可选超时)。"""
        job = self.get(job_id)
        if job is None:
            return None
        if job.thread is not None:
            job.thread.join(timeout=timeout)
        else:
            deadline = None if timeout is None else time.time() + timeout
            while True:
                current = self._store.get(job_id)
                if current is None or current.get("status") not in ("queued", "running", "cancel_requested"):
                    if current:
                        job.status = current.get("status", job.status)
                        job.result = current.get("result", job.result)
                        job.error = current.get("error", job.error)
                    break
                if deadline is not None and time.time() >= deadline:
                    break
                time.sleep(0.1)
        return job

    def prune_finished(self) -> int:
        """清理已结束的任务记录 (保留会话文件)。返回移除数量。"""
        removed = 0
        with self._lock:
            for jid in [j.job_id for j in self._jobs.values() if j.status != "running"]:
                self._jobs.pop(jid, None)
                removed += 1
        return removed

    # -------------------------------------------------------- 多任务并行

    def submit_parallel(
        self,
        tasks: List[str],
        confirm: Optional[Callable[[str], bool]] = None,
        stream: bool = False,
        on_progress: Optional[Callable[[str, str], None]] = None,
        exclude_tools: tuple = (),
    ) -> List[BackgroundJob]:
        """并行提交多个后台任务, 立即返回所有 job 句柄。

        与逐个调用 submit 的区别:
        - 原子性: 一次调用提交全部, 避免中途失败导致部分提交;
        - 聚合状态: status_all() 可以一键查看全部任务的进度。
        """
        jobs: List[BackgroundJob] = []
        for task in tasks:
            if not task or not task.strip():
                continue
            job = self.submit(
                task.strip(), confirm=confirm, stream=stream,
                on_progress=on_progress, exclude_tools=exclude_tools,
            )
            jobs.append(job)
        return jobs

    def status_all(self) -> Dict[str, Any]:
        """聚合查看所有任务的状态概览。"""
        jobs = self.list_jobs()
        by_status: Dict[str, int] = {}
        for j in jobs:
            by_status[j.status] = by_status.get(j.status, 0) + 1
        return {
            "total": len(jobs),
            "by_status": by_status,
            "jobs": [j.to_dict() for j in jobs[:20]],
        }

    def recover_queued(self) -> List[str]:
        """CLI 进程重启后, 重启所有 queued 且无人接管的任务 (崩溃恢复)。

        场景: 上一个 CLI 进程在 submit_detached 后崩溃, worker 根本没起来; 或 worker
        进程已死但 manifest 仍停在 queued。返回成功重启的任务 job_id 列表。
        """
        restarted: List[str] = []
        for job_id in self._store.recoverable():
            data = self._store.get(job_id)
            if data is None:
                continue
            task = data.get("task", "")
            workspace = data.get("workspace", self.workspace)
            try:
                relaunched = self.submit_detached(
                    task,
                    yolo=bool(data.get("yolo", False)),
                )
                # submit_detached 已写新 worker pid; 补 workspace (recover 时尽量还原)。
                self._store.update(relaunched["job_id"], workspace=workspace)
                restarted.append(job_id)
            except Exception as exc:  # noqa: BLE001
                self._store.update(job_id, status="failed",
                                   error=f"恢复重启失败: {type(exc).__name__}: {exc}")
        return restarted


# ---------------------------------------------------------------- CLI 便捷入口

def submit_background(kernel: Kernel, agent: Agent, task: str, workspace: str) -> str:
    """模块级便捷函数: 提交后台任务并返回 job_id (供 cli/commands 调用)。

    使用 submit_detached 启动独立 worker 进程, 确保任务在 CLI 退出后仍能完成。

    仅当配置显式允许 `background.allow_thread_fallback` (测试/嵌入环境) 时, 才在
    detached 真正不可用的情况下降级为进程内 daemon 线程 —— 该线程会随 CLI 退出被杀,
    故绝不作静默默认。其他任何 detached 失败都直接上浮, 让 CLI 如实报错。
    """
    config = kernel.require("config")
    runner = BackgroundRunner(kernel, config, workspace)
    try:
        data = runner.submit_detached(task)
        return str(data["job_id"])
    except Exception as exc:
        if config.get("background.allow_thread_fallback", False):
            log.warning("submit_detached 失败, 降级为进程内 daemon 线程 "
                        "(任务不会在 CLI 退出后存活): %s", exc)
            return runner.submit(task).job_id
        raise


def wait_for_job(kernel: Kernel, job_id: str, timeout: float = 0) -> str:
    """模块级便捷函数: 阻塞等待后台任务结束, 返回结果文本 (供 cli/commands 调用)。"""
    config = kernel.require("config")
    workspace = getattr(config, "workspace", None) or os.getcwd()
    runner = BackgroundRunner(kernel, config, workspace)
    job = runner.wait(job_id, timeout=timeout or None)
    if job is None:
        return f"任务 {job_id} 未找到"
    return job.result or job.error or "(无输出)"
