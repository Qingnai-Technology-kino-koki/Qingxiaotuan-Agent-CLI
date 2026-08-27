"""Workspace Trust — 项目目录信任系统 (对标 Claude Code 2.1.239 Workspace Trust)。

核心概念:
- 每个项目目录有一个信任级别, 决定 Agent 在该目录中的权限
- 信任级别: trusted | limited | untrusted | unknown
- 首次打开项目时提示用户确认信任
- 信任状态持久化到 QXT_HOME/workspace_trust.json
- 支持组织级默认信任策略 (managed settings)

安全边界:
- untrusted: 禁止执行 shell 命令, 只读工具可用
- limited: shell 命令需每次确认, 写工具受限
- trusted: 完整权限 (仍受 safety 引擎保护)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


# ================================================================ 信任级别

class TrustLevel:
    TRUSTED = "trusted"       # 完整权限
    LIMITED = "limited"       # 受限权限 (shell 需确认)
    UNTRUSTED = "untrusted"   # 只读
    UNKNOWN = "unknown"       # 未评估 (首次打开)


# ================================================================ 信任记录

@dataclass
class TrustRecord:
    """单个目录的信任记录。"""
    path: str
    level: str = TrustLevel.UNKNOWN
    trusted_at: float = 0.0
    last_used: float = 0.0
    project_name: str = ""
    git_remote: str = ""       # git remote URL (用于识别项目)
    trust_count: int = 0       # 累计信任次数

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "level": self.level,
            "trusted_at": self.trusted_at,
            "last_used": self.last_used,
            "project_name": self.project_name,
            "git_remote": self.git_remote,
            "trust_count": self.trust_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrustRecord":
        return cls(
            path=str(data.get("path", "")),
            level=str(data.get("level", TrustLevel.UNKNOWN)),
            trusted_at=float(data.get("trusted_at", 0)),
            last_used=float(data.get("last_used", 0)),
            project_name=str(data.get("project_name", "")),
            git_remote=str(data.get("git_remote", "")),
            trust_count=int(data.get("trust_count", 0)),
        )


# ================================================================ 信任管理器

class WorkspaceTrust:
    """项目目录信任管理器。

    用法:
        trust = WorkspaceTrust(Path("~/.qingxiaotuan"))
        level = trust.check_trust("/path/to/project")
        if level == TrustLevel.UNKNOWN:
            # 提示用户确认
            trust.set_trust("/path/to/project", TrustLevel.TRUSTED)
    """

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self._trust_file = self.home / "workspace_trust.json"
        self._records: Dict[str, TrustRecord] = {}
        self._load()

    def _load(self) -> None:
        if self._trust_file.exists():
            try:
                data = json.loads(self._trust_file.read_text(encoding="utf-8"))
                for path, record in data.items():
                    self._records[path] = TrustRecord.from_dict(record)
            except Exception:
                self._records = {}

    def _save(self) -> None:
        data = {path: r.to_dict() for path, r in self._records.items()}
        self._trust_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _normalize_path(self, path: str) -> str:
        """规范化路径 (resolve symlinks, expanduser)。"""
        try:
            return str(Path(path).expanduser().resolve())
        except Exception:
            return str(Path(path).resolve())

    def check_trust(self, path: str) -> str:
        """检查目录的信任级别。"""
        normalized = self._normalize_path(path)
        record = self._records.get(normalized)
        if record is None:
            return TrustLevel.UNKNOWN
        return record.level

    def set_trust(self, path: str, level: str, project_name: str = "", git_remote: str = "") -> TrustRecord:
        """设置目录的信任级别。"""
        normalized = self._normalize_path(path)
        now = time.time()
        existing = self._records.get(normalized)

        record = TrustRecord(
            path=normalized,
            level=level,
            trusted_at=now if level == TrustLevel.TRUSTED else (existing.trusted_at if existing else 0),
            last_used=now,
            project_name=project_name or (existing.project_name if existing else ""),
            git_remote=git_remote or (existing.git_remote if existing else ""),
            trust_count=(existing.trust_count + 1) if existing and level == TrustLevel.TRUSTED else 1,
        )
        self._records[normalized] = record
        self._save()
        return record

    def update_last_used(self, path: str) -> None:
        """更新最后使用时间。"""
        normalized = self._normalize_path(path)
        record = self._records.get(normalized)
        if record:
            record.last_used = time.time()
            self._save()

    def is_trusted(self, path: str) -> bool:
        """快速检查是否已信任。"""
        return self.check_trust(path) == TrustLevel.TRUSTED

    def is_readonly(self, path: str) -> bool:
        """快速检查是否只读。"""
        return self.check_trust(path) == TrustLevel.UNTRUSTED

    def list_trusted(self) -> List[TrustRecord]:
        """列出所有已信任的目录。"""
        return [r for r in self._records.values() if r.level == TrustLevel.TRUSTED]

    def remove(self, path: str) -> bool:
        """移除信任记录。"""
        normalized = self._normalize_path(path)
        if normalized in self._records:
            del self._records[normalized]
            self._save()
            return True
        return False

    def cleanup(self, max_age_days: int = 90) -> int:
        """清理长期未使用的信任记录。"""
        cutoff = time.time() - (max_age_days * 86400)
        stale = [p for p, r in self._records.items()
                 if r.last_used < cutoff and r.level != TrustLevel.TRUSTED]
        for p in stale:
            del self._records[p]
        if stale:
            self._save()
        return len(stale)

    def get_project_info(self, path: str) -> Dict[str, Any]:
        """获取项目的信任信息 (供 UI 展示)。"""
        normalized = self._normalize_path(path)
        record = self._records.get(normalized)
        if record is None:
            return {
                "path": normalized,
                "trust_level": TrustLevel.UNKNOWN,
                "needs_confirmation": True,
            }
        return {
            "path": normalized,
            "trust_level": record.level,
            "project_name": record.project_name,
            "git_remote": record.git_remote,
            "trusted_at": record.trusted_at,
            "last_used": record.last_used,
            "trust_count": record.trust_count,
            "needs_confirmation": record.level == TrustLevel.UNKNOWN,
        }
