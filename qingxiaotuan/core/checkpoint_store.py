"""自动检查点存储 (对标 Claude Code 2.1.246 的 Checkpointing / Rewind)。

能力对齐:
  - 自动建检查点: 每次写工具成功后, 基于事务账本自动落一个检查点 (无需手动 save)。
  - 跨会话持久化: 检查点元数据落在 <workspace>/.qxt/checkpoints/, 跨进程可用, 30 天 TTL。
  - 变更文件追踪: 每个检查点记录其发生以来被触碰的文件清单。
  - 三种恢复策略: both(对话+代码) / code(仅代码回滚) / conversation(仅对话截回)。
  - 摘要 (synthesize from here): 从指定检查点把对话压缩为 AI 摘要, 释放上下文。

依赖: MutationLedger (core/ledger) 提供字节级快照与 undo_since; 本存储只负责
“何时自动建点 / 如何持久化 / 分模式恢复 / 摘要” 的编排。零第三方依赖。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

CHECKPOINTS_SUBDIR = ".qxt/checkpoints"
TTL_SECONDS = 30 * 24 * 3600  # 30 天


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    try:
        return config.get(key, default)
    except Exception as exc:
        log.debug("读取配置 %s 失败: %s", key, exc)
        return default


class CheckpointStore:
    """自动检查点存储。绑定一个工作区 + 一个账本实例。"""

    def __init__(self, workspace: str, ledger: Any = None, config: Any = None,
                 subdir: Optional[str] = None, ttl: Optional[int] = None,
                 max_checkpoints: Optional[int] = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.ledger = ledger
        self.enabled = _cfg_get(config, "checkpoint.enabled", True)
        self.auto = _cfg_get(config, "checkpoint.auto", True)   # 每次写工具后自动建点
        self.ttl = int(ttl if ttl is not None else _cfg_get(config, "checkpoint.ttl_seconds", TTL_SECONDS))
        self.max_checkpoints = int(max_checkpoints if max_checkpoints is not None
                                   else _cfg_get(config, "checkpoint.max_count", 50))
        if subdir is None:
            subdir = _cfg_get(config, "checkpoint.subdir", CHECKPOINTS_SUBDIR)
        self.dir = self.workspace / subdir
        self._index_path = self.dir / "checkpoints.json"
        self._index: List[Dict[str, Any]] = []
        self._last_mark: Optional[int] = getattr(ledger, "_seq", None) if ledger else None
        if self.enabled:
            self._load()

    # ---------------------------------------------------------- persistence

    def _load(self) -> None:
        try:
            if self._index_path.exists():
                with open(self._index_path, "r", encoding="utf-8") as fh:
                    self._index = json.load(fh) or []
        except Exception as exc:
            log.warning("检查点索引读取失败: %s", exc)
            self._index = []
        self._prune_expired()

    def _flush(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._index_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._index, fh, ensure_ascii=False)
            tmp.replace(self._index_path)
        except Exception as exc:
            log.warning("检查点索引落盘失败: %s", exc)

    def _prune_expired(self) -> None:
        """清理过期 (超 TTL) 与超量检查点, 保持索引有界。"""
        now = time.time()
        keep: List[Dict[str, Any]] = []
        for cp in self._index:
            age = now - float(cp.get("created_at", 0))
            if cp.get("ledger_marker") is not None and age <= self.ttl:
                keep.append(cp)
        # 按时间排序后保留最近 max_checkpoints 个
        keep.sort(key=lambda c: float(c.get("created_at", 0)))
        for old in keep[:-self.max_checkpoints] if self.max_checkpoints > 0 else []:
            self._remove_cp(old)
        keep = keep[-self.max_checkpoints:] if self.max_checkpoints > 0 else keep
        self._index = keep
        # 顺带清理孤儿文件
        cids = {c["id"] for c in self._index}
        if self.dir.exists():
            for mdf in self.dir.glob("cp_*.md"):
                stem = mdf.name.removeprefix("cp_").removesuffix(".md")
                if stem not in cids:
                    try:
                        mdf.unlink()
                    except Exception:
                        pass

    def _remove_cp(self, cp: Dict[str, Any]) -> None:
        try:
            (self.dir / f"cp_{cp['id']}.md").unlink(missing_ok=True)
        except Exception:
            pass

    # ---------------------------------------------------------------- API

    def _next_id(self) -> str:
        """基于现存 id 数值后缀取 max+1, 避免切点/超量剪枝后 id 碰撞覆盖。"""
        n = 0
        for cp in self._index:
            i = cp.get("id", "")
            if i.startswith("ckp"):
                try:
                    n = max(n, int(i[3:]))
                except (ValueError, TypeError):
                    continue
        return f"ckp{n + 1}"

    def auto_checkpoint(self, files: List[str], summary: str = "") -> Optional[Dict[str, Any]]:
        """写工具成功后的自动建点。记录 (mark 标记, 触碰文件, 摘要)。

        files: 本次触碰的工作区相对路径。
        """
        if not self.enabled or not self.auto:
            return None
        marker = self.ledger.mark() if self.ledger is not None else None
        cp: Dict[str, Any] = {
            "id": self._next_id(),
            "created_at": time.time(),
            "ledger_marker": marker,
            "files": sorted({f.strip().lstrip("./\\") for f in files if f}),
            "summary": summary or "",
        }
        self._index.append(cp)
        self._write_summary(cp)
        self._prune_expired()
        self._flush()
        log.debug("自动检查点 %s 已建立 (files=%s)", cp["id"], len(cp["files"]))
        return cp

    def _write_summary(self, cp: Dict[str, Any]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            lines = [
                f"# Checkpoint {cp['id']}",
                f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cp['created_at']))}",
                f"- 变更文件: {', '.join(cp['files']) or '(无)'}",
                f"- 摘要: {cp['summary'] or '(无)'}",
            ]
            (self.dir / f"cp_{cp['id']}.md").write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:
            log.debug("检查点摘要落盘失败: %s", exc)

    def list(self) -> List[Dict[str, Any]]:
        self._prune_expired()
        return list(reversed(self._index))  # 新的在前

    def find(self, checkpoint_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if checkpoint_id:
            for cp in self._index:
                if cp["id"] == checkpoint_id:
                    return cp
            return None
        return self._index[-1] if self._index else None

    def restore(self, checkpoint_id: str = "", *,
                mode: str = "both",
                undo_cb: Optional[Callable[[int], List[str]]] = None,
                truncate_cb: Optional[Callable[[int], int]] = None,
                target_msg_len: Optional[int] = None) -> str:
        """分模式恢复到指定检查点。

        mode:
          both          对话+代码都回滚 (默认)
          code          仅回滚该点之后的文件变更 (对话保留)
          conversation  仅把对话流截回该点 (代码保留)

        undo_cb(marker): 回滚到账本标记者; truncate_cb(len): 截对话流到长度。
        任一回调缺失时, 对应部分被跳过。
        """
        cp = self.find(checkpoint_id)
        if cp is None:
            return "没有可恢复的检查点。" if not checkpoint_id else f"未找到检查点 {checkpoint_id}"
        marker = cp.get("ledger_marker")
        parts: List[str] = []

        do_code = mode in ("both", "code") and undo_cb is not None and marker is not None
        do_conv = mode in ("both", "conversation") and truncate_cb is not None and target_msg_len is not None

        if do_code:
            undone = undo_cb(int(marker))
            if undone:
                parts.append(f"文件回滚 {len(undone)} 项")

        if do_conv:
            n = truncate_cb(int(target_msg_len))
            parts.append(f"对话截回 {n} 条")

        if not parts:
            return f"检查点 {cp['id']}: 该模式下无需变更。"

        # 时间线已重写: 丢弃该点及其后的检查点
        idx = next((i for i, c in enumerate(self._index) if c["id"] == cp["id"]), None)
        if idx is not None:
            self._index = self._index[:idx]
        self._flush()
        return f"已恢复到检查点 {cp['id']} ({mode}): " + "; ".join(parts)

    def summarize_from(self, checkpoint_id: str = "",
                       context_summary: Callable[[int], str] = None) -> str:
        """从指定检查点开始把之前的对话压缩为摘要 (synthesize from here)。

        仅生成摘要文本, 不改变磁盘文件; truncation_cb 不足以切出边界时可配合
        context 压缩回调实现。返回摘要报告。
        """
        cp = self.find(checkpoint_id)
        if cp is None:
            return "没有可摘要的检查点。"
        n_files = len(cp.get("files", []))
        head = f"检查点 {cp['id']}: 自该点起共 {n_files} 次文件变更"
        if context_summary is not None:
            head += "\n" + (context_summary(int(cp.get("ledger_marker", -1))) or "")
        return head

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto": self.auto,
            "ttl_seconds": self.ttl,
            "max_checkpoints": self.max_checkpoints,
            "count": len(self._index),
            "dir": str(self.dir),
        }

    def clear(self) -> int:
        n = len(self._index)
        self._index = []
        try:
            if self.dir.exists():
                for f in self.dir.iterdir():
                    if f.name.startswith("cp_"):
                        f.unlink(missing_ok=True)
        except Exception as exc:
            log.debug("清理检查点失败: %s", exc)
        self._flush()
        return n