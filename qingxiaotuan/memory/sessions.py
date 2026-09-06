"""会话存储 —— Harness 风格 append-only 事件流 + 会话恢复 + 导出。

每个会话是一个 JSONL 文件: 每行一个事件 (user / assistant / tool_call / tool_result …),
天然支持审计、回放、断点续聊。

增强 (对标 Claude Code 2.1.214):
- export_markdown: 导出会话为 Markdown 格式, 便于分享和归档。
- export_summary: 导出会话摘要 (用户/助手交替)。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, cast


class SessionStore:
    def __init__(self, home: Path) -> None:
        self.dir = home / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.file = self.dir / f"{self.session_id}.jsonl"
        # 多线程并发 append 同一 JSONL, open("a")+write 不原子, 实测 200 次并发
        # 只落盘 139 行 (30%+ 丢失) 且部分行被截断。审计/事件溯源直接破坏。
        self._lock = threading.Lock()

    def append(self, event_type: str, payload: Dict[str, Any]) -> None:
        record = {"ts": time.time(), "type": event_type, **payload}
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()

    def rotate(self, session_id: Optional[str] = None,
               meta: Optional[Dict[str, Any]] = None) -> str:
        """开启一个新会话文件 (同一 home 下), 返回新 session_id。

        用途: 让每次 `Agent.run()` 拥有独立的 JSONL, 从而 `qxt replay` 能逐会话回放,
        而进程级共享的 SessionStore 仍作为默认/兜底。

        若传入 meta, 会在文件首行写入 session.meta (供 peek_title/区分后台会话)。
        """
        sid = session_id or (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
        self.session_id = sid
        self.file = self.dir / f"{sid}.jsonl"
        if meta is not None:
            self.append("session.meta", {"task": meta.get("task", ""),
                                         "started_at": time.time(),
                                         **{k: v for k, v in meta.items() if k != "task"}})
        return sid

    def read_all(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """读取全部事件记录 (规整前)。空文件/损坏行返回 []。"""
        if not self.file.exists():
            return []
        out: List[Dict[str, Any]] = []
        with open(self.file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if limit is not None and len(out) >= limit:
                    break
        return out

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

    @classmethod
    def sum_usage(cls, path: Path) -> Dict[str, Any]:
        """聚合会话流中的 usage 增量事件 (对标 Claude Code 的 /cost 跨会话统计)。

        返回:
        {
          "prompt_tokens": ..., "completion_tokens": ...,
          "prompt_cache_hit_tokens": ..., "prompt_cache_miss_tokens": ...,
          "cost_usd": 累计成本 (缺定价的增量按 0 计),
          "calls": 调用次数,
          "models": {"provider/model": tokens 总数, ...},
        }
        无 usage 事件时返回空 dict。
        """
        totals: Dict[str, int] = {}
        cost = 0.0
        calls = 0
        models: Dict[str, int] = {}
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "usage":
                        continue
                    calls += 1
                    delta = rec.get("delta") or {}
                    for key, val in delta.items():
                        if isinstance(val, (int, float)):
                            totals[key] = totals.get(key, 0) + int(val)
                    c = rec.get("cost_usd")
                    if isinstance(c, (int, float)):
                        cost += float(c)
                    label = f"{rec.get('provider', '')}/{rec.get('model', '')}"
                    models[label] = models.get(label, 0) + sum(
                        int(v) for v in delta.values() if isinstance(v, (int, float))
                    )
        except OSError:
            return {}
        if not calls:
            return {}
        result: Dict[str, Any] = {**totals, "cost_usd": round(cost, 6),
                                  "calls": calls, "models": models}
        return result

    def list_sessions(self) -> List[Path]:
        # stat 包在循环内逐个兜底: 遍历间隙被清理掉的会话文件直接跳过,
        # 否则一个 FileNotFoundError 会让整个列表命令崩掉。
        stamped: List[tuple[float, Path]] = []
        for p in self.dir.glob("*.jsonl"):
            try:
                stamped.append((p.stat().st_mtime, p))
            except OSError:
                continue
        stamped.sort(key=lambda item: item[0], reverse=True)
        return [p for _, p in stamped]

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
                        return cast(Dict[str, Any], rec)
                    # meta 通常写在最前; 遇到第一条非 meta 业务事件即可停止扫描
                    break
        except OSError:
            return None
        return None

    # ------------------------------------------------------------------ 分叉

    def fork(self, at_message: Optional[int] = None, note: str = "") -> "SessionStore":
        """分叉当前会话: 复制截至第 ``at_message`` 条 user/assistant 消息的历史。

        生成一个 child session (新文件), 其 ``session.meta`` 记录分叉谱系
        ``(kind=fork, parent, branch_point)``, 供 `session tree` 追踪分支关系。
        若 ``at_message`` 为 None, 分叉整份当前会话。
        """
        child = SessionStore(self.dir)
        # fork 应把子会话写在同一会话目录, 而非再套一层 sessions/sessions
        child.dir = self.dir
        child.file = self.dir / f"{child.session_id}.jsonl"
        parent_meta = self.read_meta(self.file) or {}
        meta_payload: Dict[str, Any] = {
            "kind": "fork",
            "parent": self.session_id,
            "branch_point": at_message,
        }
        if note:
            meta_payload["note"] = note
        if parent_meta.get("task"):
            meta_payload["task"] = parent_meta["task"]
        child.append("session.meta", meta_payload)
        counts = 0
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # 内容事件 (user/assistant/tool...) 才计数; session.meta 不进子会话
                    if rec.get("type") == "session.meta":
                        continue
                    if rec.get("type") in ("user", "assistant") and "message" in rec:
                        counts += 1
                        if at_message is not None and counts > at_message:
                            break
                    payload = {k: v for k, v in rec.items() if k not in ("ts", "type")}
                    child.append(rec.get("type") or "event", payload)
        except OSError:
            pass
        return child

    def branch_info(self) -> Dict[str, Any]:
        """返回当前会话的分叉谱系信息 (无则空 dict)。"""
        meta = self.read_meta(self.file) or {}
        if meta.get("kind") != "fork":
            return {}
        return {
            "kind": "fork",
            "parent": meta.get("parent"),
            "branch_point": meta.get("branch_point"),
            "note": meta.get("note", ""),
        }

    @classmethod
    def peek_title(cls, path: Path) -> str:
        """从会话流里取一个可读标题: 优先 session.meta.task, 否则首条 user 消息。"""
        meta = cls.read_meta(path)
        if meta and meta.get("task"):
            return str(meta["task"])[:60].replace("\n", " ")
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "user" and rec.get("message", {}).get("content"):
                        return str(rec["message"]["content"])[:60].replace("\n", " ")
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
