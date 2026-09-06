"""MCP 调用审计日志持久化 —— 内存审计日志之外落盘到 JSONL, 供 /mcp audit 跨进程查看。

存储位置: ~/.qingxiaotuan/mcp-audit.jsonl (受 QXT_HOME 影响, 与配置/白名单同目录)。
每条记录一行 JSON: {timestamp, server, tool, arguments(已脱敏), success, result_length,
sensitive_params, note}。

线程安全: 追加写入用进程内锁串行化; 每条记录单行, 进程崩溃也不会破坏既有记录。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_path() -> Path:
    try:
        from ...config.loader import home_dir

        return home_dir() / "mcp-audit.jsonl"
    except Exception:  # noqa: BLE001 - 配置模块不可用时回退到 ~/.qingxiaotuan
        return Path(os.environ.get("QXT_HOME", Path.home() / ".qingxiaotuan")) / "mcp-audit.jsonl"


class MCPAuditStore:
    """MCP 调用审计的 JSONL 持久化存储。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else _default_path()
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, entry: Dict[str, Any]) -> None:
        """追加一条审计记录。失败静默 (审计不阻断主流程)。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False, default=str)
            with self._lock, open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    def read(self, limit: int = 100) -> List[Dict[str, Any]]:
        """读取最近的 limit 条记录 (新记录在前)。"""
        if not self._path.exists():
            return []
        try:
            with self._lock, open(self._path, "r", encoding="utf-8") as f:
                lines = [ln for ln in f if ln.strip()]
        except OSError:
            return []
        entries: List[Dict[str, Any]] = []
        for ln in lines[-limit:]:
            try:
                entries.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return list(reversed(entries))

    def clear(self) -> None:
        """清空审计日志。"""
        try:
            with self._lock:
                self._path.unlink(missing_ok=True)
        except OSError:
            pass

    def count(self) -> int:
        """统计总记录数。"""
        if not self._path.exists():
            return 0
        try:
            with self._lock, open(self._path, "r", encoding="utf-8") as f:
                return sum(1 for ln in f if ln.strip())
        except OSError:
            return 0
