"""WireStore —— append-only JSONL 持久化）。

record 类型（type 字段）：
- context.append_message    { message: ContextMessageDict }
- context.append_loop_event { event: LoopRecordedEventDict }
- context.apply_compaction  { summary / contextSummary / compactedCount / ... }
- context.undo              { count: int }
- context.clear             {}

replay(journal) 可离线重建 ContextMemory。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List

from .contracts import ContextMessage, LoopRecordedEvent
from .memory import ContextMemory


class WireStore:
    """基于 JSONL 的 append-only 记录存储。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- 写入
    def append_record(self, record: Dict[str, Any]) -> None:
        line = {"type": record["type"], "time": record.get("time", int(time.time() * 1000))}
        for key, value in record.items():
            if key not in ("type", "time"):
                line[key] = value
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------- 读取
    def read_journal(self) -> Iterator[Dict[str, Any]]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or not isinstance(record.get("type"), str):
                    continue
                yield record

    # ---------------------------------------------------------- 封口
    def seal(self) -> None:
        """若文件为空，写入一条 metadata 记录作封口。"""
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        metadata = {
            "type": "metadata",
            "protocol_version": "1.5",
            "created_at": int(time.time() * 1000),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(metadata, ensure_ascii=False) + "\n")

    def exists(self) -> bool:
        return self.path.exists()


# ============================================================ 离线重建

def replay(journal: Iterator[Dict[str, Any]]) -> ContextMemory:
    """从 wire journal 重建 ContextMemory。"""
    memory = ContextMemory()
    for record in journal:
        rtype = record.get("type")
        if rtype == "context.append_message":
            raw = record.get("message")
            if isinstance(raw, dict):
                memory.append(ContextMessage.from_dict(raw))
        elif rtype == "context.append_loop_event":
            raw = record.get("event")
            if isinstance(raw, dict):
                memory.append_loop_event(LoopRecordedEvent.from_dict(raw))
        elif rtype == "context.apply_compaction":
            memory.apply_compaction(record)
        elif rtype == "context.undo":
            count = record.get("count", 1)
            if isinstance(count, int):
                memory.undo(count)
        elif rtype == "context.clear":
            memory.clear()
        # metadata / 未知类型：忽略
    return memory


def replay_file(path: Path) -> ContextMemory:
    """从 JSONL 文件重建 ContextMemory。"""
    store = WireStore(path)
    return replay(store.read_journal())
