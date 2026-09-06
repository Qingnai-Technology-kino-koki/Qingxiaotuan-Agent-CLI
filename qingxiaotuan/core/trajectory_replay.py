"""会话轨迹回放 —— 基于 append-only 会话事件流的 Trajectory 视图。

核心理念:
- **单一真相源**: 所有事件 (system/assistant/tool/user/security) 写入同一事件流
- **按来源筛选**: 可按事件类型、工具名、安全决策等筛选回放
- **分叉**: 从任意断点分叉出新的回放分支
- **断点恢复**: 从保存的快照恢复回放状态
- **统计分析**: 工具调用统计、安全决策统计、耗时分析

与现有模块的关系:
- session_store: 追加写会话事件 (数据源)
- trajectory.py: 轨迹快照 (已有)
- trajectory_replay: 交互式回放与分析 (本模块)

用法::

    from qingxiaotuan.core.trajectory_replay import TrajectoryReplay

    replay = TrajectoryReplay(session_dir=Path("~/.qingxiaotuan/sessions"))

    # 列出会话
    sessions = replay.list_sessions()

    # 回放一个会话
    events = replay.replay("20260822-190957-1142a0")

    # 按来源筛选
    tool_events = replay.replay("session_id", filter_type="tool_call")

    # 统计分析
    stats = replay.analyze("session_id")
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)


# ============================================================ 数据类

@dataclass
class TrajectoryEvent:
    """单条轨迹事件。"""
    seq: int
    ts: float
    event_type: str                # user / assistant / tool_call / tool / system / security / verify
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""               # 来源模块 (shell / mcp / safety / ...)
    tool_name: str = ""            # 工具名 (如有)
    duration_ms: float = 0.0       # 耗时 (毫秒)
    status: str = "ok"             # ok / error / denied / timeout

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq, "ts": self.ts, "event_type": self.event_type,
            "payload": self.payload, "source": self.source,
            "tool_name": self.tool_name, "duration_ms": self.duration_ms,
            "status": self.status,
        }


@dataclass
class TrajectoryStats:
    """轨迹统计结果。"""
    total_events: int = 0
    total_duration_ms: float = 0.0
    by_type: Dict[str, int] = field(default_factory=dict)
    by_tool: Dict[str, int] = field(default_factory=dict)
    by_status: Dict[str, int] = field(default_factory=dict)
    by_source: Dict[str, int] = field(default_factory=dict)
    tool_latencies: Dict[str, List[float]] = field(default_factory=dict)
    security_events: int = 0
    denied_events: int = 0
    error_events: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_events": self.total_events,
            "total_duration_ms": self.total_duration_ms,
            "by_type": self.by_type,
            "by_tool": self.by_tool,
            "by_status": self.by_status,
            "by_source": self.by_source,
            "avg_tool_latency": {
                k: sum(v) / len(v) if v else 0
                for k, v in self.tool_latencies.items()
            },
            "security_events": self.security_events,
            "denied_events": self.denied_events,
            "error_events": self.error_events,
        }


@dataclass
class TrajectorySnapshot:
    """回放快照 (用于断点恢复 / 分叉)。"""
    session_id: str
    fork_point: int                # 分叉点序号
    events: List[TrajectoryEvent]
    created_at: float = 0.0
    label: str = ""

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()


# ============================================================ 轨迹回放器

class TrajectoryReplay:
    """会话轨迹回放器。

    用法::

        replay = TrajectoryReplay(session_dir=Path("~/.qingxiaotuan/sessions"))

        # 列出会话
        sessions = replay.list_sessions()

        # 回放
        events = replay.replay("session_id")

        # 按类型筛选
        tool_events = replay.replay("session_id", filter_type="tool_call")

        # 分析
        stats = replay.analyze("session_id")

        # 分叉
        fork = replay.fork("session_id", at_seq=50, label="alternative")
    """

    def __init__(self, session_dir: Optional[Path] = None) -> None:
        self._session_dir = session_dir or Path.home() / ".qingxiaotuan" / "sessions"
        self._snapshots_dir = self._session_dir / ".trajectories"
        self._cache: Dict[str, List[TrajectoryEvent]] = {}

    # ------------------------------------------------------------ 会话列表

    def list_sessions(self, last_n: int = 20) -> List[Dict[str, Any]]:
        """列出最近的会话。"""
        if not self._session_dir.exists():
            return []

        sessions = []
        for f in sorted(self._session_dir.glob("*.jsonl"), reverse=True)[:last_n]:
            sid = f.stem
            try:
                meta = self._read_session_meta(f)
                sessions.append({
                    "session_id": sid,
                    "file": str(f),
                    "event_count": meta.get("event_count", 0),
                    "last_ts": meta.get("last_ts", 0),
                    "duration_s": meta.get("duration_s", 0),
                })
            except Exception:
                sessions.append({"session_id": sid, "file": str(f)})

        return sessions

    # ------------------------------------------------------------ 回放

    def replay(
        self,
        session_id: str,
        *,
        filter_type: Optional[str] = None,
        filter_tool: Optional[str] = None,
        filter_source: Optional[str] = None,
        filter_status: Optional[str] = None,
        since_ts: Optional[float] = None,
        until_ts: Optional[float] = None,
        last_n: Optional[int] = None,
        on_event: Optional[Callable[[TrajectoryEvent], None]] = None,
    ) -> List[TrajectoryEvent]:
        """回放会话事件流, 支持多维筛选。

        Args:
            session_id: 会话 ID (文件名去 .jsonl)
            filter_type: 按事件类型筛选 (user/assistant/tool_call/tool/system/security)
            filter_tool: 按工具名筛选
            filter_source: 按来源模块筛选
            filter_status: 按状态筛选 (ok/error/denied)
            since_ts: 起始时间戳
            until_ts: 结束时间戳
            last_n: 只返回最后 N 条
            on_event: 每条事件的回调

        Returns:
            筛选后的事件列表
        """
        events = self._load_events(session_id)

        # 多维筛选
        filtered = events
        if filter_type:
            filtered = [e for e in filtered if e.event_type == filter_type]
        if filter_tool:
            filtered = [e for e in filtered if e.tool_name == filter_tool]
        if filter_source:
            filtered = [e for e in filtered if e.source == filter_source]
        if filter_status:
            filtered = [e for e in filtered if e.status == filter_status]
        if since_ts:
            filtered = [e for e in filtered if e.ts >= since_ts]
        if until_ts:
            filtered = [e for e in filtered if e.ts <= until_ts]
        if last_n:
            filtered = filtered[-last_n:]

        # 回调
        if on_event:
            for event in filtered:
                try:
                    on_event(event)
                except Exception:
                    pass

        return filtered

    # ------------------------------------------------------------ 分析

    def analyze(self, session_id: str) -> TrajectoryStats:
        """分析会话轨迹, 返回统计信息。"""
        events = self._load_events(session_id)
        stats = TrajectoryStats()
        stats.total_events = len(events)

        if not events:
            return stats

        stats.total_duration_ms = (events[-1].ts - events[0].ts) * 1000

        for event in events:
            # 按类型统计
            stats.by_type[event.event_type] = stats.by_type.get(event.event_type, 0) + 1

            # 按工具统计
            if event.tool_name:
                stats.by_tool[event.tool_name] = stats.by_tool.get(event.tool_name, 0) + 1

            # 按状态统计
            stats.by_status[event.status] = stats.by_status.get(event.status, 0) + 1

            # 按来源统计
            if event.source:
                stats.by_source[event.source] = stats.by_source.get(event.source, 0) + 1

            # 工具延迟
            if event.tool_name and event.duration_ms > 0:
                stats.tool_latencies.setdefault(event.tool_name, []).append(event.duration_ms)

            # 安全事件
            if event.event_type == "security" or event.source.startswith("security"):
                stats.security_events += 1

            # 拒绝/错误
            if event.status == "denied":
                stats.denied_events += 1
            elif event.status == "error":
                stats.error_events += 1

        return stats

    # ------------------------------------------------------------ 分叉

    def fork(
        self,
        session_id: str,
        at_seq: int,
        label: str = "",
    ) -> TrajectorySnapshot:
        """从指定断点分叉出新的回放分支。"""
        events = self._load_events(session_id)
        fork_events = [e for e in events if e.seq <= at_seq]

        snapshot = TrajectorySnapshot(
            session_id=session_id,
            fork_point=at_seq,
            events=fork_events,
            label=label or f"fork_at_{at_seq}",
        )

        # 保存快照
        self._save_snapshot(snapshot)
        return snapshot

    def restore_snapshot(self, snapshot_id: str) -> Optional[TrajectorySnapshot]:
        """从快照恢复。"""
        path = self._snapshots_dir / f"{snapshot_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            events = [TrajectoryEvent(**e) for e in data["events"]]
            return TrajectorySnapshot(
                session_id=data["session_id"],
                fork_point=data["fork_point"],
                events=events,
                created_at=data.get("created_at", 0),
                label=data.get("label", ""),
            )
        except Exception:
            return None

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """列出所有快照。"""
        if not self._snapshots_dir.exists():
            return []
        results = []
        for f in self._snapshots_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "id": f.stem,
                    "session_id": data.get("session_id", ""),
                    "fork_point": data.get("fork_point", 0),
                    "label": data.get("label", ""),
                    "created_at": data.get("created_at", 0),
                    "event_count": len(data.get("events", [])),
                })
            except Exception:
                pass
        return results

    # ------------------------------------------------------------ 导出

    def export_timeline(self, session_id: str, last_n: int = 50) -> str:
        """导出时间线格式的回放 (Markdown)。"""
        events = self.replay(session_id, last_n=last_n)
        lines = [
            f"# 会话轨迹: {session_id}",
            f"- 事件数: {len(events)}",
            "",
        ]

        for event in events:
            ts = time.strftime("%H:%M:%S", time.localtime(event.ts))
            icon = {
                "user": "👤", "assistant": "🤖", "tool_call": "🔧",
                "tool": "📦", "system": "⚙️", "security": "🛡️",
                "verify": "✅",
            }.get(event.event_type, "📌")

            status_icon = {"ok": "✓", "error": "✗", "denied": "🚫", "timeout": "⏰"}.get(event.status, "")
            tool_info = f" ({event.tool_name})" if event.tool_name else ""
            duration_info = f" [{event.duration_ms:.0f}ms]" if event.duration_ms > 0 else ""

            lines.append(f"- [{ts}] {icon} {event.event_type}{tool_info}{duration_info} {status_icon}")

            # 简要内容
            if event.event_type in ("user", "assistant"):
                # payload 可能是 {"message": {"content": ...}} 或 {"content": ...}
                msg = event.payload.get("message", event.payload)
                content = msg.get("content", "") if isinstance(msg, dict) else ""
                if content:
                    prefix = ">" if event.event_type == "user" else "<"
                    lines.append(f"  {prefix} {content[:100]}")
            elif event.event_type == "security":
                reason = event.payload.get("reason", "")
                if reason:
                    lines.append(f"  ⚠️ {reason[:100]}")

        return "\n".join(lines)

    # ------------------------------------------------------------ 内部方法

    def _load_events(self, session_id: str) -> List[TrajectoryEvent]:
        """加载会话事件。"""
        if session_id in self._cache:
            return self._cache[session_id]

        session_file = self._session_dir / f"{session_id}.jsonl"
        if not session_file.exists():
            return []

        events: List[TrajectoryEvent] = []
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                seq = 0
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        seq += 1
                        event = TrajectoryEvent(
                            seq=seq,
                            ts=data.get("ts", 0),
                            event_type=data.get("type", data.get("event_type", "unknown")),
                            payload=data.get("message", data.get("payload", {})),
                            source=data.get("source", ""),
                            tool_name=data.get("name", data.get("tool_name", "")),
                            duration_ms=data.get("duration_ms", 0),
                            status=data.get("status", "ok"),
                        )
                        events.append(event)
                    except Exception:
                        continue
        except Exception:
            pass

        self._cache[session_id] = events
        return events

    def _read_session_meta(self, path: Path) -> Dict[str, Any]:
        """读取会话元数据 (不加载全部事件)。"""
        meta = {"event_count": 0, "last_ts": 0, "duration_s": 0}
        first_ts = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    meta["event_count"] += 1
                    try:
                        data = json.loads(line.strip())
                        ts = data.get("ts", 0)
                        meta["last_ts"] = ts
                        if first_ts == 0:
                            first_ts = ts
                    except Exception:
                        continue
            if first_ts > 0 and meta["last_ts"] > 0:
                meta["duration_s"] = round(meta["last_ts"] - first_ts, 1)
        except Exception:
            pass
        return meta

    def _save_snapshot(self, snapshot: TrajectorySnapshot) -> None:
        """保存分叉快照。"""
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        sid = f"{snapshot.session_id}_fork_{snapshot.fork_point}"
        path = self._snapshots_dir / f"{sid}.json"
        data = {
            "session_id": snapshot.session_id,
            "fork_point": snapshot.fork_point,
            "events": [e.to_dict() for e in snapshot.events],
            "created_at": snapshot.created_at,
            "label": snapshot.label,
        }
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
