"""Agent View — 多会话管理面板 (对标 Claude Code claude agents)。

一屏展示所有会话状态:
- 正在运行的后台任务
- 等待用户输入的会话
- 已完成/失败的任务

用法:
  qxt agents              # 打开 Agent View 面板
  qxt agents list         # 列出所有会话
  qxt agents attach <id>  # 附着到某个会话
"""

from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import home_dir
from ..memory.sessions import SessionStore
from ._ui_singleton import ui, console


def _get_session_status(path: Path) -> str:
    """判断会话状态: running / waiting / done / failed。"""
    meta = SessionStore.read_meta(path)
    if meta:
        kind = meta.get("kind", "")
        if kind == "background":
            # 后台任务: 检查是否有最终状态
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in reversed(lines[-50:]):
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if rec.get("type") == "job.done":
                            return "done"
                        if rec.get("type") == "job.error":
                            return "failed"
                        if rec.get("type") == "job.cancel":
                            return "cancelled"
                return "running"  # 后台任务没有结束标记
            except OSError:
                return "unknown"
    
    # 交互式会话: 检查最后一条消息的时间
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                return "empty"
            last_rec = json.loads(lines[-1])
            last_type = last_rec.get("type", "")
            last_ts = last_rec.get("ts", 0)
            
            # 如果最后是 assistant 消息, 可能是完成状态
            if last_type == "assistant":
                return "done"
            # 如果最后是 user 消息, 可能在等待模型回复
            elif last_type == "user":
                return "waiting"
            # 如果最后是 tool_call, 可能在执行中
            elif last_type == "tool_call":
                return "executing"
    except (OSError, json.JSONDecodeError):
        pass
    
    return "unknown"


def _get_session_summary(path: Path, max_chars: int = 80) -> str:
    """获取会话摘要。"""
    title = SessionStore.peek_title(path)
    if title:
        return title[:max_chars]
    return "(无摘要)"


def _format_relative_time(timestamp: float) -> str:
    """格式化相对时间。"""
    delta = time.time() - timestamp
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def cmd_agents_view(args) -> int:
    """Agent View 面板: 一屏展示所有会话状态。"""
    sessions_dir = home_dir() / "sessions"
    if not sessions_dir.exists():
        console.print("没有会话记录")
        return 0
    
    files = sorted(sessions_dir.glob("*.jsonl"), 
                   key=lambda f: f.stat().st_mtime, reverse=True)
    
    if not files:
        console.print("没有会话记录")
        return 0
    
    # 分类会话
    running: List[Path] = []
    waiting: List[Path] = []
    done: List[Path] = []
    failed: List[Path] = []
    
    for f in files[:50]:  # 最多显示50个
        status = _get_session_status(f)
        if status == "running":
            running.append(f)
        elif status in ("waiting", "executing"):
            waiting.append(f)
        elif status == "done":
            done.append(f)
        elif status in ("failed", "cancelled"):
            failed.append(f)
        else:
            done.append(f)  # 其他状态归为已完成
    
    console.print("")
    console.print(f"┌─ Agent View ─────────────────────────────────────────┐")
    console.print(f"│  总计: {len(files)} 个会话                                    │")
    console.print(f"├──────────────────────────────────────────────────────┤")
    
    # 显示运行中的任务
    if running:
        console.print(f"│  ▶ Running ({len(running)})                                         │")
        console.print(f"├──────────────────────────────────────────────────────┤")
        for f in running[:10]:
            summary = _get_session_summary(f)
            mtime = _format_relative_time(f.stat().st_mtime)
            meta = SessionStore.read_meta(f)
            task = meta.get("task", "") if meta else ""
            if task:
                summary = task[:60]
            console.print(f"│  {f.stem[:20]:<20s} {summary[:30]:<30s} {mtime:>8s} │")
        if len(running) > 10:
            console.print(f"│  ...还有 {len(running) - 10} 个运行中任务                          │")
    
    # 显示等待输入的会话
    if waiting:
        console.print(f"├──────────────────────────────────────────────────────┤")
        console.print(f"│  ⏸ Waiting ({len(waiting)})                                        │")
        console.print(f"├──────────────────────────────────────────────────────┤")
        for f in waiting[:10]:
            summary = _get_session_summary(f)
            mtime = _format_relative_time(f.stat().st_mtime)
            console.print(f"│  {f.stem[:20]:<20s} {summary[:30]:<30s} {mtime:>8s} │")
        if len(waiting) > 10:
            console.print(f"│  ...还有 {len(waiting) - 10} 个等待中会话                            │")
    
    # 显示已完成的任务
    if done:
        console.print(f"├──────────────────────────────────────────────────────┤")
        console.print(f"│  ✓ Done ({len(done)})                                            │")
        console.print(f"├──────────────────────────────────────────────────────┤")
        for f in done[:15]:
            summary = _get_session_summary(f)
            mtime = _format_relative_time(f.stat().st_mtime)
            console.print(f"│  {f.stem[:20]:<20s} {summary[:30]:<30s} {mtime:>8s} │")
        if len(done) > 15:
            console.print(f"│  ...还有 {len(done) - 15} 个已完成会话                              │")
    
    # 显示失败的任务
    if failed:
        console.print(f"├──────────────────────────────────────────────────────┤")
        console.print(f"│  ✗ Failed ({len(failed)})                                          │")
        console.print(f"├──────────────────────────────────────────────────────┤")
        for f in failed[:5]:
            summary = _get_session_summary(f)
            mtime = _format_relative_time(f.stat().st_mtime)
            console.print(f"│  {f.stem[:20]:<20s} {summary[:30]:<30s} {mtime:>8s} │")
        if len(failed) > 5:
            console.print(f"│  ...还有 {len(failed) - 5} 个失败任务                               │")
    
    console.print(f"└──────────────────────────────────────────────────────┘")
    console.print("")
    console.print("用法:")
    console.print("  qxt session resume <id>    恢复会话")
    console.print("  qxt bg list                查看后台任务")
    console.print("  qxt bg logs <id>           查看任务日志")
    
    return 0


def cmd_agents(args) -> int:
    """Agent 命令入口。"""
    sub = getattr(args, "agents_cmd", None)
    
    if sub is None or sub == "view":
        return cmd_agents_view(args)
    elif sub == "list":
        # 简化版列表
        sessions_dir = home_dir() / "sessions"
        if not sessions_dir.exists():
            console.print("没有会话记录")
            return 0
        files = sorted(sessions_dir.glob("*.jsonl"), 
                       key=lambda f: f.stat().st_mtime, reverse=True)[:20]
        for i, f in enumerate(files, 1):
            title = SessionStore.peek_title(f) or "(无标题)"
            status = _get_session_status(f)
            mtime = _format_relative_time(f.stat().st_mtime)
            console.print(f"{i:2d}. [{status:^10s}] {title[:50]:<50s} {mtime}")
        return 0
    else:
        console.print(f"未知子命令: {sub}")
        console.print("可用: view / list")
        return 1
