"""定时任务存储 —— Hermes cron 的极简版。

任务存放在 ~/.qingxiaotuan/cron/jobs.json:
    {"id", "name", "prompt", "interval_minutes", "last_run", "enabled"}
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class CronStore:
    def __init__(self, home: Path) -> None:
        self.dir = home / "cron"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "jobs.json"
        # _read + _write 是非原子的读改写序列。多线程并发 add / remove 会:
        # 1) 读回同一个过期列表 → 后写覆盖前写, 任务丢失;
        # 2) 同时 os.replace 同一目标 → Windows 上 PermissionError, 任务丢失。
        # 实测: 100 次并发 add 100 个 job, 最终只保留 22 个。
        # 必须用 RLock 串行化所有读改写对。
        self._lock = threading.RLock()

    def _read(self) -> List[Dict[str, Any]]:
        if not self.file.exists():
            return []
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            # 损坏: 备份后返回空, 避免后续写覆盖丢失原始数据。
            try:
                self.file.replace(self.file.with_suffix(".corrupted.bak"))
            except OSError:
                pass
            return []

    def _write(self, jobs: List[Dict[str, Any]]) -> None:
        """临时文件 + 原子替换写入, 崩溃不留下半截 jobs.json。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="jobs.", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(jobs, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.file)
        finally:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass

    def add(self, name: str, prompt: str, interval_minutes: int) -> Dict[str, Any]:
        with self._lock:
            jobs = self._read()
            job = {
                "id": uuid.uuid4().hex[:8],
                "name": name,
                "prompt": prompt,
                "interval_minutes": interval_minutes,
                "last_run": 0,
                "enabled": True,
            }
            jobs.append(job)
            self._write(jobs)
        return job

    def remove(self, job_id: str) -> bool:
        with self._lock:
            jobs = self._read()
            kept = [j for j in jobs if j["id"] != job_id]
            self._write(kept)
        return len(kept) != len(jobs)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for j in self._read():
                if j["id"] == job_id:
                    return j
            return None

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        with self._lock:
            jobs = self._read()
            found = False
            for j in jobs:
                if j["id"] == job_id:
                    j["enabled"] = enabled
                    found = True
            if found:
                self._write(jobs)
        return found

    def update(self, job_id: str, **fields: Any) -> bool:
        with self._lock:
            jobs = self._read()
            found = False
            for j in jobs:
                if j["id"] == job_id:
                    for k, v in fields.items():
                        if v is not None:
                            j[k] = v
                    found = True
            if found:
                self._write(jobs)
        return found

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self._read()

    def due(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [
                j for j in self._read()
                if j.get("enabled") and now - j.get("last_run", 0) >= j["interval_minutes"] * 60
            ]

    def mark_run(self, job_id: str) -> None:
        with self._lock:
            jobs = self._read()
            for j in jobs:
                if j["id"] == job_id:
                    j["last_run"] = time.time()
            self._write(jobs)
