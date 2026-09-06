"""Replay —— 从历史会话事件流重建并回放 (对标 Claude Code session 回放 + DeepSeek Trajectory 复盘)。

提供:
- list_sessions(home): 列出历史会话 (id / 时间 / 标题)
- load_trajectory(id_or_path, home): 加载为 Trajectory
- reconstruct_messages(id_or_path, home): 重建 messages (供 --resume / 续聊)
- render_replay(id_or_path, home): 生成可打印的回放文本 (用户/助手/工具 时间线)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config.loader import home_dir
from ..memory.sessions import SessionStore
from .trajectory import Trajectory


def _resolve_path(id_or_path: str, home: Path) -> Optional[Path]:
    p = Path(id_or_path)
    if p.exists():
        return p
    cand = home / "sessions" / f"{id_or_path}.jsonl"
    if cand.exists():
        return cand
    # 允许前缀匹配 (用户只记得前几位)
    matches = sorted((home / "sessions").glob(f"{id_or_path}*.jsonl")) if (home / "sessions").exists() else []
    return matches[0] if matches else None


def list_sessions(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """列出历史会话: 每条含 session_id / mtime / title / 事件数。"""
    home = home or home_dir()
    sess_dir = home / "sessions"
    if not sess_dir.exists():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(sess_dir.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            n = sum(1 for _ in open(f, "r", encoding="utf-8"))
        except OSError:
            n = 0
        out.append({
            "session_id": f.stem,
            "mtime": f.stat().st_mtime,
            "mtime_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
            "title": SessionStore.peek_title(f),
            "events": n,
        })
    return out


def load_trajectory(id_or_path: str, home: Optional[Path] = None) -> Optional[Trajectory]:
    home = home or home_dir()
    path = _resolve_path(id_or_path, home)
    if path is None:
        return None
    store = SessionStore(home)
    store.file = path  # 指向目标文件而非新文件
    store.session_id = path.stem
    return Trajectory.from_session_store(store, session_id=path.stem)


def reconstruct_messages(id_or_path: str, home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """从事件流重建 messages (复用 SessionStore.load_messages)。"""
    home = home or home_dir()
    path = _resolve_path(id_or_path, home)
    if path is None:
        return []
    return SessionStore.load_messages(path)


def render_replay(id_or_path: str, home: Optional[Path] = None) -> str:
    """生成可打印的回放文本。"""
    traj = load_trajectory(id_or_path, home)
    if traj is None:
        return f"[回放] 找不到会话: {id_or_path}"
    m = traj.metrics()
    lines: List[str] = []
    lines.append(f"═══ 会话回放 · {traj.session_id} ═══")
    if traj.meta.get("task"):
        lines.append(f"任务: {traj.meta['task']}")
    lines.append(
        f"步数 {m['steps']} · 用户轮次 {m['user_turns']} · "
        f"工具 {m['tool_calls']}(✓{m['tool_ok']}/⊘{m['tool_failed']}) · "
        f"Token {m['total_tokens']} · ${m['total_cost_usd']}"
    )
    lines.append("─" * 60)
    for s in traj.steps:
        if s.kind == "message":
            who = "用户" if s.role == "user" else "青小团"
            lines.append(f"● {who}: {s.content[:400]}")
        elif s.kind == "tool":
            mark = "✓" if s.status == "completed" else ("⊘" if s.status == "failed" else "…")
            dur = f"{s.duration_ms}ms" if s.duration_ms else ""
            lines.append(f"  └ 工具 {mark} {s.tool} {dur}")
            if s.result:
                lines.append(f"      ↳ {s.result[:200]}")
        elif s.kind in ("verify", "goal", "system"):
            lines.append(f"  · {s.kind}[{s.status or s.content}]")
    lines.append("─" * 60)
    return "\n".join(lines)
