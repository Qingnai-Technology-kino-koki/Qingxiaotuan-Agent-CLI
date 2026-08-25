"""跨进程后台任务 manifest 存储。

每个任务一个 JSON 文件，写入采用临时文件替换，避免 worker 崩溃留下半条状态。
该模块不依赖 Kernel，CLI 和 worker 进程都可以安全使用。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _is_process_alive(pid: int) -> bool:
    """跨平台进程存活检查。

    注意: Windows 上 os.kill(pid, 0) 的 signal 0 等于 CTRL_C_EVENT,
    会直接向进程发送 Ctrl+C 而非探测存活 —— 必须用 OpenProcess 探测。
    """
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == 259  # STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def kill_process_tree(pid: int) -> bool:
    """跨平台终止进程及其整个子进程树, 返回是否发出过终止信号。

    优先级: 进程组级终止 (连根拔起, 避免孤儿工具子进程继续干活) > 单进程终止。
    终止是协作式的: 先 TERM/CTRL_C, 调用方负责超时后升级为 KILL。
    """
    if pid is None or pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows: taskkill /T 递归杀整棵树; /F 强制。
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            # POSIX: 杀整个进程组 (worker 以新进程组启动)。
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        # 进程已不存在, 视为已终止。
        return True
    except OSError:
        return False


class BackgroundStore:
    """管理 `~/.qingxiaotuan/background/jobs/*.json` 任务状态。"""

    def __init__(self, home: Path) -> None:
        self.root = home / "background"
        self.jobs_dir = self.root / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def create(self, job_id: str, task: str, workspace: str, profile: str = "default") -> Dict[str, Any]:
        now = time.time()
        data = {
            "job_id": job_id, "task": task, "workspace": workspace, "profile": profile,
            "status": "queued", "pid": None, "started_at": now, "updated_at": now,
            "heartbeat": now, "turns": 0, "result": "", "error": None,
            "session_file": None, "yolo": False,
        }
        self.write(data)
        return data

    def write(self, data: Dict[str, Any]) -> None:
        target = self.path(str(data["job_id"]))
        data = {**data, "updated_at": time.time()}
        fd, temp_name = tempfile.mkstemp(prefix=target.stem + ".", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self.path(job_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def list(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for path in self.jobs_dir.glob("bg-*.json"):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return sorted(result, key=lambda item: item.get("started_at", 0), reverse=True)

    def reconcile_stale(self, timeout: float = 90.0) -> int:
        """将心跳超时且进程已不存在的 running/cancel_requested 任务标记为 failed。

        返回处理数量 (queued 且无 worker 接管的任务不在此处理, 见 recoverable())。
        """
        changed = 0
        now = time.time()
        for data in self.list():
            status = data.get("status")
            if status not in ("running", "cancel_requested"):
                continue
            heartbeat = float(data.get("heartbeat", data.get("updated_at", 0)) or 0)
            pid = data.get("pid")
            alive = _is_process_alive(int(pid)) if pid else False
            if now - heartbeat > timeout and not alive:
                self.update(data["job_id"], status="failed",
                            error=f"worker 心跳超时 (>{timeout:.0f}s)")
                changed += 1
        return changed

    def recoverable(self, timeout: float = 90.0) -> List[str]:
        """返回处于 queued 状态 (worker 从未启动或已退出) 且可恢复重启的任务 job_id 列表。

        queued 任务意味着没有任何 worker 进程在跑它 (worker 启动后会立刻翻成 running
        并写入 pid); 若其 manifest 的 pid 对应的进程已不存在, 则可安全重启。
        """
        now = time.time()
        out: List[str] = []
        for data in self.list():
            if data.get("status") != "queued":
                continue
            pid = data.get("pid")
            alive = _is_process_alive(int(pid)) if pid else False
            if not alive:
                out.append(str(data["job_id"]))
        return out

    def mark_recovered(self, job_id: str, pid: int) -> Optional[Dict[str, Any]]:
        """恢复重启: 翻回 queued→running 并写入新 worker pid / 心跳。"""
        return self.update(job_id, status="running", pid=pid,
                           heartbeat=time.time(), started_at=time.time())

    def update(self, job_id: str, **changes: Any) -> Optional[Dict[str, Any]]:
        data = self.get(job_id)
        if data is None:
            return None
        data.update(changes)
        self.write(data)
        return data
