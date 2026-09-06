"""qxt replay —— 回放历史会话 (A4 事件溯源重建)。

用法:
    qxt replay                列出最近会话
    qxt replay <id>           回放指定会话 (用户/助手/工具 时间线)
    qxt replay <id> --json   以 JSON 输出 Trajectory
    qxt replay <id> --export out.md   导出为 Markdown
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..core.replay import list_sessions, load_trajectory, render_replay


def cmd_replay(args) -> int:
    if getattr(args, "list", False) or not getattr(args, "session", None):
        rows = list_sessions()
        if not rows:
            print("没有历史会话。先跑一次 `qxt` 或 `qxt run` 即可生成。")
            return 0
        print(f"{'#':>3}  {'时间':<20}  {'事件':>5}  标题")
        for i, r in enumerate(rows, 1):
            print(f"{i:>3}  {r['mtime_str']:<20}  {r['events']:>5}  {r['title'][:48]}")
        return 0

    sid = args.session
    if getattr(args, "json", False):
        traj = load_trajectory(sid)
        if traj is None:
            print(f"[错误] 找不到会话: {sid}", file=sys.stderr)
            return 1
        print(traj.to_json())
        return 0

    text = render_replay(sid)
    if getattr(args, "export", None):
        out = Path(args.export)
        out.write_text(text, encoding="utf-8")
        print(f"已导出回放: {out}")
    else:
        print(text)
    return 0
