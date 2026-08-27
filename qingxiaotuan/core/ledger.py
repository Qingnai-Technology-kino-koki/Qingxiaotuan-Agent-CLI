"""事务化操作账本 (Mutation Ledger) —— 「最小影响半径」的事后可逆闭环。

设计动机:
    青小团已有「事前拦截」(safety 引擎拦截 critical 命令) 与 Plan 模式 (只读),
    但 Agent 真正改完东西后, 用户几乎无法"精确撤销"——现有 /undo 只是 git 命令的薄封装,
    只在 git 仓库内有效、只能整库/整文件回滚, 且不记录 Agent 自身的工具级变更。

    本账本把"最小影响半径"补齐到「事后可逆 + 事前可见」:
    - 每个写类工具执行前, 先对目标文件/目录做字节级快照 (存于工作区 .qxt/ledger);
    - 执行成功后记录一条不可变变更凭证 (id/工具/目标/摘要);
    - 工具抛异常时自动从快照恢复 (事务保证, 文件系统不留半成品);
    - 支持精细回滚: 撤销最近 N 步 / 撤销指定文件 / 全部撤销。
    - 与 git 无关: 非 git 仓库也能逐文件精确回滚 (git 仅作为 shell 级回滚的兜底)。

为何几乎没有 Agent 搭载:
    主流 Agent (含 Claude Code) 的"撤销"依赖用户的版本控制习惯, 自身不维护操作级快照账本,
    更没有"执行前自动快照 + 异常自动回滚"的事务语义。这是青小团的直接差异化先机。

本模块零第三方依赖, 仅用标准库, 可在无内核/无 Agent 的环境下独立单测。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    """兼容 Config 对象与纯 dict 的读取。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    try:
        return config.get(key, default)
    except Exception as exc:
        log.debug("读取配置 %s 失败: %s", key, exc)
        return default


class MutationLedger:
    """基于快照的事务化操作账本。

    一个账本实例绑定一个工作区。快照存于 <workspace>/<snapshot_dir>/,
    变更凭证保留在内存 (受 max_records 限制), 同时可落盘 JSONL 供审计。
    """

    def __init__(self, workspace: str, config: Any = None, snapshot_dir: Optional[str] = None) -> None:
        self.workspace = Path(workspace).resolve()
        if snapshot_dir is None:
            snapshot_dir = _cfg_get(config, "ledger.snapshot_dir", ".qxt/ledger")
        self.snapshot_dir = self.workspace / snapshot_dir
        self.config = config
        self.enabled = _cfg_get(config, "ledger.enabled", True)
        self.auto_rollback = _cfg_get(config, "ledger.auto_rollback_on_error", True)
        self.keep_snapshots = _cfg_get(config, "ledger.keep_snapshots", True)
        self.max_records = int(_cfg_get(config, "ledger.max_records", 200))
        self._records: List[Dict[str, Any]] = []
        self._seq = 0
        self._snap_seq = 0  # 快照标签独立计数, 不占用变更凭证 id (保证 m0001/m0002 连续)
        # 持久化账本 (JSONL): 即使进程退出也能审计/恢复 (恢复需配合快照目录)
        self.journal_path = self.snapshot_dir / "ledger.jsonl"
        if self.enabled:
            try:
                self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                log.warning("账本快照目录创建失败 (%s): %s", self.snapshot_dir, exc)

    # ------------------------------------------------------------------ 工具

    def _next_id(self) -> str:
        self._seq += 1
        return f"m{self._seq:04d}"

    def _resolve(self, rel: str) -> Path:
        p = (self.workspace / rel)
        try:
            return p.resolve()
        except Exception:
            return p

    def _snap_file(self, src: Path, tag: str) -> Path:
        """把单个文件字节复制到快照目录, 返回快照路径。"""
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        dst = self.snapshot_dir / f"{tag}.bin"
        shutil.copyfile(src, dst)
        return dst

    def _snap_dir(self, src: Path, tag: str) -> Path:
        """把目录打包成 zip 存入快照目录, 返回 zip 路径。"""
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        dst = self.snapshot_dir / f"{tag}.zip"
        base = src.resolve()
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(base):
                for f in files:
                    fp = Path(root) / f
                    try:
                        zf.write(fp, fp.relative_to(base))
                    except Exception:
                        continue
        return dst

    # ------------------------------------------------------------------ 核心 API

    def snapshot(self, targets: List[str]) -> List[Dict[str, Any]]:
        """对一组工作区相对路径做执行前快照。

        返回 snaps: [{rel, snap(快照路径或 None), existed(bool), kind(file|dir)}]。
        调用方应在工具 handler 执行前调用, 执行成功后再 record()。
        """
        snaps: List[Dict[str, Any]] = []
        if not self.enabled:
            return snaps
        self._snap_seq += 1
        # 标签必须带时间戳: 纯实例内计数跨进程会重置 (两个会话都从 s0001 起),
        # 后写会话的快照文件会覆写先前的同名 bin/zip, 导致 undo 恢复出错误版本。
        tag = f"s{time.time_ns()}_{self._snap_seq:04d}"
        for i, rel in enumerate(targets):
            ap = self._resolve(rel)
            if ap.is_dir():
                if ap.exists():
                    snap = self._snap_dir(ap, f"{tag}_{i}")
                    snaps.append({"rel": rel, "snap": str(snap), "existed": True, "kind": "dir"})
                else:
                    snaps.append({"rel": rel, "snap": None, "existed": False, "kind": "dir"})
            else:
                if ap.exists():
                    snap = self._snap_file(ap, f"{tag}_{i}")
                    snaps.append({"rel": rel, "snap": str(snap), "existed": True, "kind": "file"})
                else:
                    snaps.append({"rel": rel, "snap": None, "existed": False, "kind": "file"})
        return snaps

    def record(self, tool: str, targets: List[str], snaps: List[Dict[str, Any]],
               summary: str = "", kind: str = "mutation") -> str:
        """登记一条变更凭证。返回记录 id。"""
        rid = self._next_id()
        rec = {
            "id": rid,
            "ts": time.time(),
            "tool": tool,
            "kind": kind,
            "summary": summary,
            "targets": list(targets),
            "snaps": snaps,
        }
        self._records.append(rec)
        # 超限: 丢弃最旧记录并清理其快照文件
        while len(self._records) > self.max_records:
            old = self._records.pop(0)
            self._cleanup_snaps(old.get("snaps") or [])
        self._append_journal(rec)
        return rid

    def _append_journal(self, rec: Dict[str, Any]) -> None:
        """追加到 JSONL 审计日志 (含快照路径, 使 undo 可跨进程持久)。"""
        if not self.enabled:
            return
        try:
            entry = {k: v for k, v in rec.items() if k != "snaps"}
            entry["snaps_meta"] = [
                {"rel": s.get("rel"), "snap": s.get("snap"),
                 "existed": s.get("existed"), "kind": s.get("kind")}
                for s in rec.get("snaps", [])
            ]
            with open(self.journal_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("账本凭证落盘失败 (%s): %s", self.journal_path, exc)

    def load_journal(self) -> int:
        """从磁盘 journal 重建内存账本 (含快照路径), 使 undo 可跨进程持久。

        会话结束后单独运行 `qxt undo` 也能精确回滚, 因为快照文件与凭证都落在
        工作区的 .qxt/ledger/ 下。返回加载的记录数。
        """
        if not self.enabled or not self.journal_path.exists():
            return 0
        try:
            with open(self.journal_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    self._records.append({
                        "id": rec.get("id"),
                        "ts": rec.get("ts", 0),
                        "tool": rec.get("tool", ""),
                        "kind": rec.get("kind", "mutation"),
                        "summary": rec.get("summary", ""),
                        "targets": rec.get("targets", []),
                        "snaps": rec.get("snaps_meta") or [],
                    })
                    rid = rec.get("id", "")
                    if rid.startswith("m"):
                        try:
                            self._seq = max(self._seq, int(rid[1:]))
                        except ValueError:
                            pass
            return len(self._records)
        except Exception:
            return 0

    def persist(self) -> None:
        """把当前内存账本重写回 journal (跨进程 undo 后保持一致性)。"""
        if not self.enabled:
            return
        try:
            tmp = self.journal_path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                for rec in self._records:
                    entry = {k: v for k, v in rec.items() if k != "snaps"}
                    entry["snaps_meta"] = [
                        {"rel": s.get("rel"), "snap": s.get("snap"),
                         "existed": s.get("existed"), "kind": s.get("kind")}
                        for s in rec.get("snaps", [])
                    ]
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            tmp.replace(self.journal_path)
        except Exception as exc:
            log.warning("账本凭证重写失败 (%s): %s", self.journal_path, exc)

    def _restore_one(self, snap: Dict[str, Any]) -> None:
        ap = self._resolve(snap["rel"])
        existed = snap.get("existed", False)
        kind = snap.get("kind", "file")
        snap_path = snap.get("snap")
        if kind == "dir":
            if existed and snap_path and os.path.exists(snap_path):
                if ap.exists():
                    shutil.rmtree(ap, ignore_errors=True)
                with zipfile.ZipFile(snap_path, "r") as zf:
                    zf.extractall(self.workspace)
            elif not existed and ap.exists():
                shutil.rmtree(ap, ignore_errors=True)
            return
        # file
        if existed and snap_path and os.path.exists(snap_path):
            ap.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snap_path, ap)
        elif not existed and ap.exists():
            try:
                ap.unlink()
            except Exception as exc:
                log.debug("回滚删除失败 (%s): %s", ap, exc)

    def restore(self, snaps: List[Dict[str, Any]]) -> None:
        """从一组快照恢复文件系统 (逆向应用)。"""
        for snap in snaps:
            try:
                self._restore_one(snap)
            except Exception as exc:
                log.warning("回滚快照失败 (%s): %s", snap.get("rel"), exc)

    def _cleanup_snaps(self, snaps: List[Dict[str, Any]]) -> None:
        if self.keep_snapshots:
            return
        for snap in snaps:
            sp = snap.get("snap")
            if sp and os.path.exists(sp):
                try:
                    os.remove(sp)
                except Exception as exc:
                    log.debug("快照清理失败 (%s): %s", sp, exc)

    def undo_last(self, n: int = 1) -> List[str]:
        """撤销最近 n 条变更 (逆序恢复), 返回人类可读描述列表。"""
        if n < 1:
            n = 1
        done: List[str] = []
        for _ in range(min(n, len(self._records))):
            rec = self._records.pop()
            try:
                self.restore(rec.get("snaps") or [])
            finally:
                self._cleanup_snaps(rec.get("snaps") or [])
            done.append(f"已撤销 [{rec['id']}] {rec['tool']}: " + ", ".join(rec.get("targets", [])))
        return done

    def undo_file(self, rel: str) -> Optional[str]:
        """撤销最近一条涉及指定文件的变更。返回描述或 None。"""
        rel_norm = rel.strip().lstrip("./\\")
        for idx in range(len(self._records) - 1, -1, -1):
            rec = self._records[idx]
            if any(t.strip().lstrip("./\\") == rel_norm for t in rec.get("targets", [])):
                try:
                    self.restore(rec.get("snaps") or [])
                finally:
                    self._cleanup_snaps(rec.get("snaps") or [])
                self._records.pop(idx)
                return f"已撤销 [{rec['id']}] {rec['tool']} 对 {rel} 的变更"
        return None

    def undo_all(self) -> List[str]:
        """撤销全部已记录变更 (逆序), 返回描述列表。"""
        done: List[str] = []
        for rec in reversed(self._records):
            try:
                self.restore(rec.get("snaps") or [])
            finally:
                self._cleanup_snaps(rec.get("snaps") or [])
            done.append(f"已撤销 [{rec['id']}] {rec['tool']}: " + ", ".join(rec.get("targets", [])))
        self._records.clear()
        return done

    def mark(self) -> int:
        """返回当前变更记录数, 作为检查点标记 (配合 undo_since 使用)。"""
        return len(self._records)

    def undo_since(self, marker: int) -> List[str]:
        """撤销 marker 之后记录的全部变更 (逆序), 返回描述列表。

        用于会话检查点恢复: 只回滚 save 之后发生的写操作, 更早的保持不动。
        """
        done: List[str] = []
        while len(self._records) > max(0, marker):
            rec = self._records.pop()
            try:
                self.restore(rec.get("snaps") or [])
            finally:
                self._cleanup_snaps(rec.get("snaps") or [])
            done.append(f"已撤销 [{rec['id']}] {rec['tool']}: " + ", ".join(rec.get("targets", [])))
        return done

    def history(self) -> List[Dict[str, Any]]:
        """返回变更凭证列表 (元数据, 供 /impact 展示)。"""
        out = []
        for rec in self._records:
            out.append({
                "id": rec["id"],
                "ts": rec["ts"],
                "tool": rec["tool"],
                "kind": rec.get("kind", "mutation"),
                "summary": rec.get("summary", ""),
                "targets": rec.get("targets", []),
            })
        return out

    def stats(self) -> Dict[str, Any]:
        """当前影响半径统计: 涉及文件数 / 操作数。"""
        files: set[str] = set()
        for rec in self._records:
            for t in rec.get("targets", []):
                files.add(t.strip().lstrip("./\\"))
        return {
            "records": len(self._records),
            "files_touched": len(files),
            "files": sorted(files),
        }

    def empty(self) -> bool:
        return len(self._records) == 0
