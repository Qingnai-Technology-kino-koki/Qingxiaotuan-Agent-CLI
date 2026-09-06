"""qxt trajectory —— Trajectory 轨迹的结构化查看/导出 (吸收 DeepSeek Harness 理念)。

用法:
    qxt trajectory show <id>           查看指标 + 摘要
    qxt trajectory export <id> [--format json|md] [--out PATH]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..core.replay import load_trajectory


def cmd_trajectory(args) -> int:
    sub = getattr(args, "traj_cmd", None)
    if sub == "export":
        traj = load_trajectory(args.session)
        if traj is None:
            print(f"[错误] 找不到会话: {args.session}", file=sys.stderr)
            return 1
        out = traj.to_json() if args.format == "json" else traj.to_markdown()
        if args.out:
            p = Path(args.out)
            p.write_text(out, encoding="utf-8")
            print(f"已导出 Trajectory: {p}")
        else:
            print(out)
        return 0

    # 默认 / show: 指标 + 摘要
    traj = load_trajectory(args.session)
    if traj is None:
        print(f"[错误] 找不到会话: {args.session}", file=sys.stderr)
        return 1
    print(json.dumps(traj.metrics(), ensure_ascii=False, indent=2))
    print("─" * 50)
    print(traj.summary())
    return 0
