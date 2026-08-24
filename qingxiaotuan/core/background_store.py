"""跨进程后台任务 manifest 存储。

每个任务一个 JSON 文件，写入采用临时文件替换，避免 worker 崩溃留下半条状态。
该模块不依赖 Kernel，CLI 和 worker 进程都可以安全使用。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


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
        """将心跳超时且进程已不存在的任务标记为 failed，返回处理数量。"""
        changed = 0
        now = time.time()
        for data in self.list():
            if data.get("status") not in ("queued", "running", "cancel_requested"):
                continue
            heartbeat = float(data.get("heartbeat", data.get("updated_at", 0)) or 0)
            pid = data.get("pid")
            alive = False
            if pid:
                try:
                    os.kill(int(pid), 0)
                    alive = True
                except (OSError, ValueError):
                    alive = False
            if now - heartbeat > timeout and not alive:
                self.update(data["job_id"], status="failed",
                            error=f"worker 心跳超时 (>{timeout:.0f}s)")
                changed += 1
        return changed

    def update(self, job_id: str, **changes: Any) -> Optional[Dict[str, Any]]:
        data = self.get(job_id)
        if data is None:
            return None
        data.update(changes)
        self.write(data)
        return data
