"""会话存储 —— Harness 风格 append-only 事件流 + 会话恢复 + 导出。

每个会话是一个 JSONL 文件: 每行一个事件 (user / assistant / tool_call / tool_result …),
天然支持审计、回放、断点续聊。

增强 (对标 Claude Code 2.1.214):
- export_markdown: 导出会话为 Markdown 格式, 便于分享和归档。
- export_summary: 导出会话摘要 (用户/助手交替)。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class SessionStore:
    def __init__(self, home: Path) -> None:
        self.dir = home / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.file = self.dir / f"{self.session_id}.jsonl"

    def append(self, event_type: str, payload: Dict[str, Any]) -> None:
        record = {"ts": time.time(), "type": event_type, **payload}
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def load_messages(path: Path) -> List[Dict[str, Any]]:
        """从事件流重建 messages (用于 --resume)。"""
        messages: List[Dict[str, Any]] = []
        if not path.exists():
            return messages
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") in ("user", "assistant", "tool") and "message" in rec:
                    messages.append(rec["message"])
        return messages

    def list_sessions(self) -> List[Path]:
        return sorted(self.dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

    @classmethod
    def read_meta(cls, path: Path) -> Optional[Dict[str, Any]]:
        """读取会话首条 session.meta 事件 (若有)。用于区分后台/交互会话、取标题。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "session.meta":
                        return rec
                    # meta 通常写在最前; 遇到第一条非 meta 业务事件即可停止扫描
                    break
        except OSError:
            return None
        return None

    @classmethod
    def peek_title(cls, path: Path) -> str:
        """从会话流里取一个可读标题: 优先 session.meta.task, 否则首条 user 消息。"""
        meta = cls.read_meta(path)
        if meta and meta.get("task"):
            return meta["task"][:60].replace("\n", " ")
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "user" and rec.get("message", {}).get("content"):
                        return rec["message"]["content"][:60].replace("\n", " ")
        except OSError:
            pass
        return ""

    # ------------------------------------------------------------------ 导出

    def export_markdown(self, path: Optional[Path] = None) -> str:
        """导出会话为 Markdown 格式 (对标 Claude Code 的 session export)。

        输出格式:
        # 对话摘要
        > 会话时间: ...
        > 文件: ...

        **用户**: ...

        **青小团**: ...
        
        ---
        *工具调用*: ...
        """
        if path is None:
            path = self.file
        if not path.exists():
            return ""
        lines: List[str] = []
        lines.append("# 对话记录")
        meta = self.read_meta(path)
        if meta:
            if meta.get("task"):
                lines.append(f"> 任务: {meta['task']}")
            if meta.get("started_at"):
                import datetime
                ts = datetime.datetime.fromtimestamp(meta["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"> 开始时间: {ts}")
        lines.append(f"> 文件: {path.name}")
        lines.append("")

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = rec.get("type")
                    if t == "user":
                        content = rec.get("message", {}).get("content", "")
                        if content:
                            lines.append(f"**用户**: {content}")
                            lines.append("")
                    elif t == "assistant":
                        content = rec.get("message", {}).get("content", "")
                        if content:
                            lines.append(f"**青小团**: {content}")
                            lines.append("")
                    elif t == "tool_call":
                        name = rec.get("name", "")
                        args = rec.get("arguments", "")
                        if name:
                            lines.append(f"*工具调用*: `{name}`")
                            lines.append("")
        except OSError:
            return ""

        return "\n".join(lines)

    def export_summary(self, path: Optional[Path] = None, max_chars: int = 2000) -> str:
        """导出会话摘要 (仅用户/助手交替, 省略工具细节)。"""
        if path is None:
            path = self.file
        if not path.exists():
            return ""
        lines: List[str] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = rec.get("type")
                    if t == "user":
                        content = rec.get("message", {}).get("content", "")
                        if content:
                            lines.append(f"Q: {content[:200]}")
                    elif t == "assistant":
                        content = rec.get("message", {}).get("content", "")
                        if content:
                            lines.append(f"A: {content[:300]}")
        except OSError:
            return ""
        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...[摘要截断]"
        return result
