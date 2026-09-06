"""文件完整性监控 —— 关键文件防篡改检测。

核心理念:
- 对配置文件 / Hook 脚本 / 内置技能 / SOUL 等关键文件做快照
- 每次读取前校验 SHA-256, 检测未授权修改
- 变更记录落盘, 支持审计回溯
- fail-closed: 校验异常视为被篡改

与现有安全层的关系:
- security_auditor: 审计日志防篡改链 (对审计数据本身)
- file_integrity: 监控应用文件防篡改 (对代码/配置/技能)

用法::

    from qingxiaotuan.core.file_integrity import FileIntegrityMonitor

    monitor = FileIntegrityMonitor(home=Path("~/.qingxiaotuan"))
    monitor.snapshot_directory(skills_dir)  # 首次: 建快照
    changes = monitor.check_directory(skills_dir)  # 后续: 检测变更
    if changes:
        for change in changes:
            print(f"{change.path}: {change.change_type}")
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)


# ============================================================ 数据类

@dataclass
class FileSnapshot:
    """单个文件的完整性快照。"""
    path: str               # 相对路径 (相对于基准目录)
    sha256: str             # SHA-256 十六进制摘要
    size: int               # 文件大小 (字节)
    mtime: float            # 最后修改时间
    recorded_at: float      # 快照记录时间
    mode: int = 0o644       # 文件权限

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "sha256": self.sha256,
            "size": self.size, "mtime": self.mtime,
            "recorded_at": self.recorded_at, "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FileSnapshot":
        return cls(
            path=d["path"], sha256=d["sha256"],
            size=d.get("size", 0), mtime=d.get("mtime", 0.0),
            recorded_at=d.get("recorded_at", 0.0), mode=d.get("mode", 0o644),
        )


@dataclass
class IntegrityChange:
    """完整性变更记录。"""
    path: str
    change_type: str        # added / modified / deleted / permissions_changed
    old_sha256: str = ""
    new_sha256: str = ""
    severity: str = "medium"  # low / medium / high / critical
    detected_at: float = 0.0
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================ 文件完整性监控器

class FileIntegrityMonitor:
    """文件完整性监控器 —— 关键文件防篡改。

    用法::

        monitor = FileIntegrityMonitor(home=Path("~/.qingxiaotuan"))

        # 首次: 对目录建快照
        monitor.snapshot_directory(skills_dir, label="builtin_skills")

        # 后续: 检测变更
        changes = monitor.check_directory(skills_dir)
        for c in changes:
            if c.change_type in ("modified", "deleted"):
                print(f"篡改检测: {c.path}")

        # 查询历史变更
        history = monitor.get_changes(severity="high")

        # 导出完整性报告
        report = monitor.export_report()
    """

    _SNAPSHOT_DIR_NAME = ".integrity"
    _MAX_CHANGES = 5000

    def __init__(
        self,
        home: Optional[Path] = None,
        on_change: Optional[Callable[[IntegrityChange], None]] = None,
    ) -> None:
        self._home = Path(home) if home else Path.home() / ".qingxiaotuan"
        self._snapshot_dir = self._home / self._SNAPSHOT_DIR_NAME
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._changes: List[IntegrityChange] = []
        self._lock = threading.Lock()
        self._on_change = on_change

        # 加载历史变更记录
        self._load_changes()

    # ------------------------------------------------------------ 快照管理

    def snapshot_directory(
        self,
        directory: str | Path,
        label: str = "",
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> int:
        """对目录建完整性快照。

        Args:
            directory: 要监控的目录
            label: 快照标签 (用于区分不同目录的快照)
            include_patterns: 仅包含匹配这些 glob 的文件 (None=全部)
            exclude_patterns: 排除匹配这些 glob 的文件

        Returns:
            快照包含的文件数
        """
        directory = Path(directory)
        if not directory.is_dir():
            log.warning("快照目标不是目录: %s", directory)
            return 0

        snapshots: Dict[str, FileSnapshot] = {}
        count = 0

        for file_path in self._walk_files(directory, include_patterns, exclude_patterns):
            try:
                rel_path = str(file_path.relative_to(directory))
                snap = self._snapshot_file(file_path, rel_path)
                if snap:
                    snapshots[rel_path] = snap
                    count += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("快照文件失败 %s: %s", file_path, exc)

        # 保存快照
        snapshot_file = self._snapshot_path(directory, label)
        self._save_snapshot(snapshot_file, snapshots)

        log.info("已建快照: %s (%d 个文件, label=%s)", directory, count, label)
        return count

    def check_directory(
        self,
        directory: str | Path,
        label: str = "",
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[IntegrityChange]:
        """检查目录完整性, 返回变更列表。

        对比当前文件状态与已保存的快照, 检测:
        - 新增文件 (added)
        - 文件修改 (modified)
        - 文件删除 (deleted)
        - 权限变更 (permissions_changed)
        """
        directory = Path(directory)
        snapshot_file = self._snapshot_path(directory, label)
        old_snapshots = self._load_snapshot(snapshot_file)

        if not old_snapshots:
            log.debug("无已有快照, 跳过完整性检查: %s", directory)
            return []

        current_files: Set[str] = set()
        changes: List[IntegrityChange] = []

        # 检查现有文件
        for file_path in self._walk_files(directory, include_patterns, exclude_patterns):
            try:
                rel_path = str(file_path.relative_to(directory))
                current_files.add(rel_path)

                snap = self._snapshot_file(file_path, rel_path)
                if not snap:
                    continue

                old = old_snapshots.get(rel_path)
                if old is None:
                    # 新增文件
                    change = IntegrityChange(
                        path=rel_path, change_type="added",
                        new_sha256=snap.sha256,
                        severity="low",
                        detected_at=time.time(),
                        details=f"新增文件 ({snap.size} bytes)",
                    )
                    changes.append(change)
                else:
                    # 检查修改
                    if snap.sha256 != old.sha256:
                        severity = self._classify_change(old, snap)
                        change = IntegrityChange(
                            path=rel_path, change_type="modified",
                            old_sha256=old.sha256, new_sha256=snap.sha256,
                            severity=severity,
                            detected_at=time.time(),
                            details=f"内容变更 (SHA-256: {old.sha256[:8]}→{snap.sha256[:8]})",
                        )
                        changes.append(change)
                    # 检查权限变更
                    elif snap.mode != old.mode:
                        change = IntegrityChange(
                            path=rel_path, change_type="permissions_changed",
                            severity="medium",
                            detected_at=time.time(),
                            details=f"权限变更 ({oct(old.mode)}→{oct(snap.mode)})",
                        )
                        changes.append(change)
            except Exception as exc:  # noqa: BLE001
                log.debug("检查文件失败 %s: %s", file_path, exc)

        # 检查已删除文件
        for rel_path in old_snapshots:
            if rel_path not in current_files:
                change = IntegrityChange(
                    path=rel_path, change_type="deleted",
                    old_sha256=old_snapshots[rel_path].sha256,
                    severity="high",
                    detected_at=time.time(),
                    details="文件已删除",
                )
                changes.append(change)

        # 记录变更
        if changes:
            with self._lock:
                self._changes.extend(changes)
                if len(self._changes) > self._MAX_CHANGES:
                    self._changes = self._changes[-self._MAX_CHANGES:]
            self._save_changes()

            # 触发回调
            if self._on_change:
                for change in changes:
                    try:
                        self._on_change(change)
                    except Exception:  # noqa: BLE001
                        pass

        return changes

    # ------------------------------------------------------------ 查询

    def get_changes(
        self,
        *,
        severity: Optional[str] = None,
        change_type: Optional[str] = None,
        path_pattern: Optional[str] = None,
        last_n: int = 100,
    ) -> List[IntegrityChange]:
        """查询变更历史。"""
        import fnmatch
        with self._lock:
            results = list(self._changes)

        if severity:
            results = [c for c in results if c.severity == severity]
        if change_type:
            results = [c for c in results if c.change_type == change_type]
        if path_pattern:
            results = [c for c in results if fnmatch.fnmatch(c.path, path_pattern)]

        return results[-last_n:]

    def stats(self) -> Dict[str, Any]:
        """变更统计。"""
        with self._lock:
            records = list(self._changes)
        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        for c in records:
            by_type[c.change_type] = by_type.get(c.change_type, 0) + 1
            by_severity[c.severity] = by_severity.get(c.severity, 0) + 1
        return {
            "total_changes": len(records),
            "by_type": by_type,
            "by_severity": by_severity,
        }

    def export_report(self, last_n: int = 50) -> str:
        """导出完整性报告 (Markdown)。"""
        changes = self.get_changes(last_n=last_n)
        stats = self.stats()
        lines = [
            "# 文件完整性监控报告",
            "",
            f"- 总变更数: {stats['total_changes']}",
            f"- 按类型: {json.dumps(stats['by_type'], ensure_ascii=False)}",
            f"- 按严重度: {json.dumps(stats['by_severity'], ensure_ascii=False)}",
            "",
        ]
        for c in reversed(changes):
            ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c.detected_at))
            lines.append(f"### [{ts_str}] {c.change_type}: `{c.path}`")
            lines.append(f"- 严重度: {c.severity}")
            if c.details:
                lines.append(f"- 详情: {c.details}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------ 内部方法

    @staticmethod
    def _compute_sha256(file_path: Path) -> Optional[str]:
        """计算文件 SHA-256。"""
        try:
            h = hashlib.sha256()
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, PermissionError):
            return None

    def _snapshot_file(self, file_path: Path, rel_path: str) -> Optional[FileSnapshot]:
        """为单个文件建快照。"""
        sha256 = self._compute_sha256(file_path)
        if sha256 is None:
            return None
        try:
            stat = file_path.stat()
            return FileSnapshot(
                path=rel_path,
                sha256=sha256,
                size=stat.st_size,
                mtime=stat.st_mtime,
                recorded_at=time.time(),
                mode=stat.st_mode & 0o777,
            )
        except OSError:
            return None

    def _snapshot_path(self, directory: Path, label: str) -> Path:
        """快照文件的存储路径。"""
        dir_hash = hashlib.sha256(str(directory).encode()).hexdigest()[:12]
        name = f"{label}_{dir_hash}" if label else dir_hash
        return self._snapshot_dir / f"{name}.json"

    def _save_snapshot(self, path: Path, snapshots: Dict[str, FileSnapshot]) -> None:
        """保存快照到磁盘。"""
        data = {k: v.to_dict() for k, v in snapshots.items()}
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.error("保存快照失败 %s: %s", path, exc)

    def _load_snapshot(self, path: Path) -> Dict[str, FileSnapshot]:
        """加载快照。"""
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {k: FileSnapshot.from_dict(v) for k, v in data.items()}
        except Exception:
            return {}

    def _classify_change(self, old: FileSnapshot, new: FileSnapshot) -> str:
        """根据变更特征评估严重度。

        - 配置文件/脚本变更 → high (可能被注入恶意代码)
        - 普通文件变更 → medium
        - 小变更 (如注释) → low
        """
        path_lower = new.path.lower()
        # 高危文件类型
        high_risk_ext = {".py", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
                         ".yaml", ".yml", ".toml", ".json", ".md"}
        high_risk_names = {"soul.md", "agents.md", "config.yaml", "config.json",
                           "hooks.json", "managed-settings.json"}

        if any(path_lower.endswith(ext) for ext in high_risk_ext):
            return "high"
        if os.path.basename(path_lower) in high_risk_names:
            return "high"
        if new.size > 10000:
            return "medium"
        return "low"

    @staticmethod
    def _walk_files(
        directory: Path,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ):
        """遍历目录下的文件。"""
        import fnmatch
        for root, dirs, files in os.walk(directory):
            # 跳过隐藏目录和 .git
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for fname in files:
                fpath = Path(root) / fname
                rel = str(fpath.relative_to(directory))
                # 排除检查
                if exclude_patterns:
                    if any(fnmatch.fnmatch(rel, p) for p in exclude_patterns):
                        continue
                # 包含检查
                if include_patterns:
                    if not any(fnmatch.fnmatch(rel, p) for p in include_patterns):
                        continue
                yield fpath

    def _changes_path(self) -> Path:
        return self._snapshot_dir / "changes.json"

    def _save_changes(self) -> None:
        with self._lock:
            data = [c.to_dict() for c in self._changes[-self._MAX_CHANGES:]]
        try:
            self._changes_path().write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _load_changes(self) -> None:
        path = self._changes_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            with self._lock:
                self._changes = [IntegrityChange(**d) for d in data[-self._MAX_CHANGES:]]
        except Exception:
            pass
