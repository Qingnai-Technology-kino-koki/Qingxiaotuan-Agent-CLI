"""工作区隔离: direct(直接+限路径) 与 copy-diff-apply(副本→diff→确认→apply) 融合。

"取精华去糟粕":
  - 低危/良性操作走 direct —— 工作区直接读写, 仅把路径限界在工作区内(快、透明, 不打扰)。
  - 高危/不可逆操作走 copy-diff-apply —— 先在隔离副本里跑, 结束后比对变更, 由用户确认
    apply 才写回真实工作区(慢但可回滚, 破坏性操作有后悔药)。

Snapshotter 提供基于内容哈希的工作区差异与回放, 供高层隔离执行模块调用。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

_IGNORED = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv",
            "node_modules", ".qxt-sandbox", ".idea", ".vscode"}


# ---------------------------------------------------------------- 路径限界 (direct)
def resolve_in_workspace(workspace: str, path: str) -> str:
    """把 path 解析到工作区内, 越界即抛 ValueError (限路径的核心)。"""

    def _norm(p: str) -> str:
        return os.path.normcase(os.path.realpath(p))

    root = _norm(workspace)
    cand = Path(path)
    if not cand.is_absolute():
        cand = Path(workspace) / cand
    resolved = _norm(str(cand))
    if not (resolved == root or resolved.startswith(root + os.sep)):
        raise ValueError(f"路径越出工作区边界: {path}")
    return resolved


@dataclass
class Snapshot:
    """一个工作区的内容指纹: relpath -> sha1 (只记录普通文件)。"""

    root: str
    files: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def capture(cls, workspace: str, ignore: Optional[set] = None) -> "Snapshot":
        root = os.path.realpath(workspace)
        ignore = ignore or _IGNORED
        snap = cls(root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in ignore and not d.startswith(".") or d == "."]
            # 过滤隐藏/忽略目录 (保留普通隐藏文件)
            dirnames[:] = [d for d in dirnames if d not in ignore]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                try:
                    snap.files[rel] = _sha1(full)
                except OSError:
                    continue
        return snap

    def diff(self, other: "Snapshot") -> List[Dict[str, str]]:
        """对比另一个快照, 返回变更列表 (added/modified/deleted)。"""
        changes: List[Dict[str, str]] = []
        for rel, sha in self.files.items():
            if rel not in other.files:
                changes.append({"path": rel, "status": "deleted"})
            elif other.files[rel] != sha:
                changes.append({"path": rel, "status": "modified"})
        for rel in other.files:
            if rel not in self.files:
                changes.append({"path": rel, "status": "added"})
        return sorted(changes, key=lambda c: c["path"])

    def apply(self, other: "Snapshot", *, dry_run: bool = False,
              on_apply: Optional[Callable[[str, str], None]] = None,
              backup: bool = True) -> List[str]:
        """把 other 的状态回放到 self 的工作区 (即 apply).

        other 通常是"隔离副本运行后"的新快照。dry_run=True 只统计。
        """
        applied: List[str] = []
        root = Path(self.root)
        for rel, sha in other.files.items():
            src = os.path.join(other.root, rel)
            dst = os.path.join(root, rel)
            if rel in self.files and self.files[rel] == sha:
                continue  # 无变化
            if dry_run:
                applied.append(rel)
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if backup and os.path.exists(dst):
                _backup(dst)
            try:
                shutil.copy2(src, dst)
            except OSError:
                continue
            applied.append(rel)
            if on_apply:
                on_apply(rel, "modified")
        # 删除隔离副本里不存在的工作区文件
        for rel in list(self.files):
            if rel not in other.files:
                dst = os.path.join(root, rel)
                if dry_run:
                    applied.append(rel)
                    continue
                if os.path.exists(dst):
                    if backup:
                        _backup(dst)
                    try:
                        os.unlink(dst)
                    except OSError:
                        continue
                applied.append(rel)
                if on_apply:
                    on_apply(rel, "deleted")
        return applied


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup(path: str) -> None:
    bakdir = os.path.join(tempfile.gettempdir(), "qxt-sandbox-backup")
    os.makedirs(bakdir, exist_ok=True)
    name = os.path.basename(path) + ".bak"
    try:
        shutil.copy2(path, os.path.join(bakdir, name))
    except OSError:
        pass


def plan_isolation(severity: str, *, mode: str = "auto") -> str:
    """按严重度选择工作区隔离模式 (融合决策的入口)。

    mode: auto(按严重度) / direct(强制 direct) / copy_diff_apply(强制副本回放)。
    低危(<=medium) → direct; 高危/critical 且配 copy_diff_apply → copy_diff_apply。
    """
    if mode != "auto":
        return mode
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return "copy_diff_apply" if rank.get(severity, 0) >= 3 else "direct"


class WorkdirIsolation:
    """direct 模式: 真工作区 + 路径限界 (不复制)。"""

    def resolve(self, workspace: str, path: str) -> str:
        return resolve_in_workspace(workspace, path)


@dataclass
class IsolatedWorkdir:
    """copy-diff-apply 模式的一次隔离会话。

    用法:
        wd = isolate_workdir(workspace)     # 复制出独立副本, 并记录原始快照
        run_command_in(wd.path, cmd)        # (由高层执行端调用)
        changes = wd.diff()                 # 比对隔离副本与原始工作区的变更
        wd.apply()                          # 把副本变更回写真实工作区
        wd.cleanup()                        # 无论成败都清理副本
    """

    original: str
    path: str
    _base: Optional[Snapshot] = None

    def solid(self) -> "IsolatedWorkdir":
        self._base = Snapshot.capture(self.original)
        return self

    def diff(self) -> List[Dict[str, str]]:
        assert self._base is not None, "未 solid(), 无法 diff"
        return self._base.diff(Snapshot.capture(self.path))

    def apply(self, *, dry_run: bool = False) -> List[str]:
        assert self._base is not None, "未 solid(), 无法 apply"
        base_orig = Snapshot(self.original, dict(self._base.files))
        return base_orig.apply(Snapshot.capture(self.path), dry_run=dry_run)

    def cleanup(self) -> None:
        try:
            shutil.rmtree(self.path, ignore_errors=True)
        except Exception:  # pragma: no cover
            pass


def isolate_workdir(workspace: str, parent: Optional[str] = None) -> IsolatedWorkdir:
    """在工作区旁创建隔离副本目录, 并记录原始快照。"""
    base = parent or os.path.dirname(os.path.abspath(workspace))
    import uuid
    dest = os.path.join(base, f".qxt-sandbox-{uuid.uuid4().hex[:8]}")
    os.makedirs(dest, exist_ok=True)
    wd = IsolatedWorkdir(original=os.path.realpath(workspace), path=dest)
    _copy_tree(workspace, dest)
    wd.solid()
    return wd


def _copy_tree(src: str, dst: str) -> None:
    src_p = Path(src)
    for item in src_p.iterdir():
        if item.name in _IGNORED or item.name.startswith(".qxt-sandbox"):
            continue
        d_item = Path(dst) / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, d_item, ignore=shutil.ignore_patterns(
                    *_IGNORED))
            else:
                shutil.copy2(item, d_item)
        except (OSError, shutil.Error):
            continue