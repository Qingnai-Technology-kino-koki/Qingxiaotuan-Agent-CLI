"""事件溯源 (Event Sourcer) —— 单一真相源 + 分叉/恢复/回放。

借鉴 DeepSeek Harness 的理念: 模型看到的每一条 (系统提示、CoT、工具、子代理调度、上下文注入)
都写入同一份 append-only 事件流, 恢复/分叉/检索/回放共享它。

本模块在现有 SessionStore (append-only JSONL) 之上提供:
1. EventSourcer: 统一的事件写入/读取/查询接口 (单一真相源)
2. ForkPoint: 分叉点标记 (从任意事件位置 fork 出新会话)
3. Replay: 从分叉点重放历史事件到新会话

事件语义 (5 种, 借鉴 Harness):
- emit:      普通事件, 追加到事件流 (默认)
- parallel:  并行事件组 (多个子代理同时执行)
- serial:    串行事件序列 (严格顺序依赖)
- bail:      短路事件 (遇到则中止当前循环)
- waterfall: 瀑布管线 (pre-execute → execute → post-execute, 不可重排)

用法::

    sourcer = EventSourcer(session_store)

    # 写入事件
    sourcer.emit("tool_call", {"name": "run_shell", "args": "echo hello"})

    # 查询事件
    events = sourcer.query(kind="tool_call", since=last_ts)

    # 分叉
    fork_id = sourcer.fork(at_seq=42, reason="尝试不同方案")

    # 重放
    sourcer.replay(fork_id, to_session_store=new_store)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class EventSemantics(str, Enum):
    """事件语义枚举 —— 5 种原子行为。"""
    EMIT = "emit"                # 普通追加
    PARALLEL = "parallel"        # 并行组 (多子代理)
    SERIAL = "serial"            # 串行序列 (严格顺序)
    BAIL = "bail"                # 短路中止
    WATERFALL = "waterfall"      # 瀑布管线 (pre→exec→post)


@dataclass
class SourcedEvent:
    """溯源事件 —— 事件流中的一个原子记录。"""
    seq: int                      # 全局递增序号
    ts: float                     # 时间戳
    kind: str                     # 事件类型 (tool_call / assistant / user / ...)
    payload: Dict[str, Any]       # 事件数据
    session_id: str = ""          # 所属会话
    fork_id: str = ""             # 分叉 ID (空=原始会话)
    parent_seq: int = 0           # 父事件序号 (分叉点)
    semantics: str = "emit"       # 事件语义
    tags: List[str] = field(default_factory=list)  # 标签 (用于筛选/回放)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "seq": self.seq, "ts": self.ts, "type": self.kind,
            "session_id": self.session_id, "fork_id": self.fork_id,
            "parent_seq": self.parent_seq, "semantics": self.semantics,
        }
        if self.tags:
            d["tags"] = self.tags
        d.update(self.payload)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SourcedEvent":
        payload = {k: v for k, v in d.items()
                   if k not in ("seq", "ts", "type", "session_id", "fork_id",
                                "parent_seq", "semantics", "tags")}
        return cls(
            seq=d.get("seq", 0), ts=d.get("ts", 0.0),
            kind=d.get("type", ""), payload=payload,
            session_id=d.get("session_id", ""),
            fork_id=d.get("fork_id", ""),
            parent_seq=d.get("parent_seq", 0),
            semantics=d.get("semantics", "emit"),
            tags=d.get("tags", []),
        )


@dataclass
class ForkPoint:
    """分叉点 —— 从原始会话的某个位置 fork 出新会话。"""
    fork_id: str                  # 分叉 ID
    source_session: str           # 源会话 ID
    at_seq: int                   # 分叉位置的事件序号
    reason: str = ""              # 分叉原因
    created_at: float = 0.0       # 创建时间

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fork_id": self.fork_id, "source_session": self.source_session,
            "at_seq": self.at_seq, "reason": self.reason,
            "created_at": self.created_at,
        }


class EventSourcer:
    """事件溯源器 —— 统一的事件写入/读取/查询/分叉/回放接口。

    在 SessionStore (append-only JSONL) 之上提供结构化操作。
    """

    def __init__(self, session_store=None, home=None):
        """
        Args:
            session_store: 现有 SessionStore 实例 (可选, 若提供则直接操作其文件)
            home: qxt_home 路径 (session_store 为空时使用)
        """
        self._store = session_store
        self._home = home
        self._seq_counter = 0
        self._events: List[SourcedEvent] = []
        self._forks: Dict[str, ForkPoint] = {}

        # 如果有 store, 从现有事件流加载序号
        if session_store is not None:
            self._load_from_store()

    def _load_from_store(self):
        """从 SessionStore 加载现有事件, 初始化序号计数器。"""
        if self._store is None:
            return
        try:
            records = self._store.read_all()
            for r in records:
                ev = SourcedEvent.from_dict(r)
                if ev.seq > self._seq_counter:
                    self._seq_counter = ev.seq
                self._events.append(ev)
        except Exception:
            pass

    # ---------------------------------------------------------- 写入

    def emit(
        self,
        kind: str,
        payload: Dict[str, Any],
        *,
        session_id: str = "",
        fork_id: str = "",
        parent_seq: int = 0,
        semantics: str = "emit",
        tags: Optional[List[str]] = None,
    ) -> SourcedEvent:
        """写入一个溯源事件。"""
        self._seq_counter += 1
        ev = SourcedEvent(
            seq=self._seq_counter,
            ts=time.time(),
            kind=kind,
            payload=payload,
            session_id=session_id or (self._store.session_id if self._store else ""),
            fork_id=fork_id,
            parent_seq=parent_seq,
            semantics=semantics,
            tags=tags or [],
        )
        self._events.append(ev)

        # 同步写入 SessionStore (append-only)
        if self._store is not None:
            self._store.append(ev.to_dict())

        return ev

    def emit_parallel(self, events: List[Tuple[str, Dict[str, Any]]], **kwargs) -> List[SourcedEvent]:
        """写入并行事件组 (多个子代理同时执行)。"""
        results = []
        for kind, payload in events:
            ev = self.emit(kind, payload, semantics="parallel", **kwargs)
            results.append(ev)
        return results

    def emit_waterfall(self, stages: List[Tuple[str, Dict[str, Any]]], **kwargs) -> List[SourcedEvent]:
        """写入瀑布管线 (pre→exec→post, 不可重排)。"""
        results = []
        for kind, payload in stages:
            ev = self.emit(kind, payload, semantics="waterfall", **kwargs)
            results.append(ev)
        return results

    def emit_bail(self, kind: str, payload: Dict[str, Any], **kwargs) -> SourcedEvent:
        """写入短路事件 (遇到则中止当前循环)。"""
        return self.emit(kind, payload, semantics="bail", **kwargs)

    # ---------------------------------------------------------- 查询

    def query(
        self,
        *,
        kind: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        since_seq: int = 0,
        until_seq: int = 0,
        tags: Optional[List[str]] = None,
        fork_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[SourcedEvent]:
        """查询事件流, 支持按类型/时间/序号/标签/fork 过滤。"""
        results = []
        for ev in self._events:
            if kind and ev.kind != kind:
                continue
            if since and ev.ts < since:
                continue
            if until and ev.ts > until:
                continue
            if since_seq and ev.seq <= since_seq:
                continue
            if until_seq and ev.seq >= until_seq:
                continue
            if tags and not any(t in ev.tags for t in tags):
                continue
            if fork_id is not None and ev.fork_id != fork_id:
                continue
            results.append(ev)
            if len(results) >= limit:
                break
        return results

    def get_event(self, seq: int) -> Optional[SourcedEvent]:
        """按序号获取单个事件。"""
        for ev in self._events:
            if ev.seq == seq:
                return ev
        return None

    def count(self, kind: Optional[str] = None) -> int:
        """统计事件数量。"""
        if kind is None:
            return len(self._events)
        return sum(1 for ev in self._events if ev.kind == kind)

    # ---------------------------------------------------------- 分叉

    def fork(self, at_seq: int, reason: str = "") -> str:
        """从指定序号位置 fork 出新会话, 返回 fork_id。

        分叉点之后的事件可以被 replay 到新会话。
        """
        fork_id = f"fork-{uuid.uuid4().hex[:8]}"
        source_session = self._store.session_id if self._store else ""

        fp = ForkPoint(
            fork_id=fork_id,
            source_session=source_session,
            at_seq=at_seq,
            reason=reason,
            created_at=time.time(),
        )
        self._forks[fork_id] = fp

        # 记录分叉事件
        self.emit(
            "session.fork",
            {"fork_id": fork_id, "source_session": source_session,
             "at_seq": at_seq, "reason": reason},
            fork_id=fork_id,
            parent_seq=at_seq,
        )

        return fork_id

    def get_fork(self, fork_id: str) -> Optional[ForkPoint]:
        """获取分叉点信息。"""
        return self._forks.get(fork_id)

    def list_forks(self) -> List[ForkPoint]:
        """列出所有分叉点。"""
        return list(self._forks.values())

    # ---------------------------------------------------------- 回放

    def replay(
        self,
        fork_id: str,
        to_session_store=None,
        *,
        from_seq: Optional[int] = None,
        to_seq: Optional[int] = None,
    ) -> List[SourcedEvent]:
        """从分叉点重放历史事件到新会话。

        从 fork_id 对应的 at_seq 开始, 把事件复制到 to_session_store。
        """
        fp = self._forks.get(fork_id)
        if fp is None:
            raise ValueError(f"未知分叉点: {fork_id}")

        start_seq = from_seq or fp.at_seq
        end_seq = to_seq or self._seq_counter

        # 收集要重放的事件
        to_replay = [
            ev for ev in self._events
            if start_seq < ev.seq <= end_seq and ev.fork_id != fork_id
        ]

        # 写入目标 store
        if to_session_store is not None:
            for ev in to_replay:
                replayed = SourcedEvent(
                    seq=ev.seq, ts=ev.ts, kind=ev.kind,
                    payload=ev.payload,
                    session_id=getattr(to_session_store, "session_id", ""),
                    fork_id=fork_id,
                    parent_seq=ev.seq,
                    semantics=ev.semantics,
                    tags=ev.tags + ["replayed"],
                )
                to_session_store.append(replayed.to_dict())

        return to_replay

    def resume_from(self, fork_id: str) -> List[Dict[str, Any]]:
        """从分叉点恢复会话状态 (返回 messages 列表, 供 Agent 续聊)。"""
        fp = self._forks.get(fork_id)
        if fp is None:
            raise ValueError(f"未知分叉点: {fork_id}")

        # 从事件流重建 messages
        messages = []
        for ev in self._events:
            if ev.seq <= fp.at_seq:
                if ev.kind == "user":
                    messages.append({"role": "user", "content": ev.payload.get("content", "")})
                elif ev.kind == "assistant":
                    messages.append({"role": "assistant", "content": ev.payload.get("content", "")})
                elif ev.kind == "tool_call":
                    messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": ev.payload.get("toolCallId", ""),
                            "function": {
                                "name": ev.payload.get("name", ""),
                                "arguments": ev.payload.get("arguments", ""),
                            },
                        }],
                    })
                elif ev.kind == "tool_call_update":
                    messages.append({
                        "role": "tool",
                        "content": ev.payload.get("result", ""),
                    })
        return messages

    # ---------------------------------------------------------- 导出

    def to_trajectory(self):
        """导出为 Trajectory 对象 (复用 trajectory.py)。"""
        from .trajectory import Trajectory
        records = [ev.to_dict() for ev in self._events]
        return Trajectory.from_records(records, session_id=self._get_session_id())

    def export_markdown(self) -> str:
        """导出为 Markdown 格式的轨迹报告。"""
        traj = self.to_trajectory()
        return traj.to_markdown()

    def export_dict(self) -> Dict[str, Any]:
        """导出为结构化字典。"""
        return {
            "session_id": self._get_session_id(),
            "total_events": len(self._events),
            "forks": {k: v.to_dict() for k, v in self._forks.items()},
            "events": [ev.to_dict() for ev in self._events],
            "metrics": self.to_trajectory().metrics(),
        }

    def _get_session_id(self) -> str:
        if self._store is not None:
            return getattr(self._store, "session_id", "")
        return ""
