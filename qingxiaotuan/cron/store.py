"""定时任务存储 —— Hermes cron 的极简版。

任务存放在 ~/.qingxiaotuan/cron/jobs.json:
    {"id", "name", "prompt", "interval_minutes", "last_run", "enabled"}
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List


class CronStore:
    def __init__(self, home: Path) -> None:
        self.dir = home / "cron"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "jobs.json"

    def _read(self) -> List[Dict[str, Any]]:
        if not self.file.exists():
            return []
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _write(self, jobs: List[Dict[str, Any]]) -> None:
        self.file.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, name: str, prompt: str, interval_minutes: int) -> Dict[str, Any]:
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
        jobs = self._read()
        kept = [j for j in jobs if j["id"] != job_id]
        self._write(kept)
        return len(kept) != len(jobs)

    def list(self) -> List[Dict[str, Any]]:
        return self._read()

    def due(self) -> List[Dict[str, Any]]:
        now = time.time()
        return [
            j for j in self._read()
            if j.get("enabled") and now - j.get("last_run", 0) >= j["interval_minutes"] * 60
        ]

    def mark_run(self, job_id: str) -> None:
        jobs = self._read()
        for j in jobs:
            if j["id"] == job_id:
                j["last_run"] = time.time()
        self._write(jobs)
