"""gh 安全审计日志 —— 把每次拦截/确认事件落盘, 供事后追踪。

所有破坏性拦截 (FORBIDDEN) 与被取消的受保护操作 (GUARDED denied) 都会写一条
JSON 记录到 ``~/.qingxiaotuan/logs/gh-audit.log``。审计只做**记录**, 绝不影响
安全拦截本身的判定 (写盘失败静默忽略)。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

_lock = threading.Lock()
_FORMAT = "%Y-%m-%dT%H:%M:%S"


def audit_log_path(home: Optional[Path] = None) -> Path:
    if home is None:
        from ..config.loader import home_dir
        home = home_dir()
    return Path(home) / "logs" / "gh-audit.log"


def append_event(
    argv: List[str],
    decision: str,
    event: str = "blocked",
    detail: str = "",
    home: Optional[Path] = None,
    now: Optional[float] = None,
) -> Path:
    """追加一条审计记录 (线程安全), 返回日志路径。写盘失败静默忽略。"""
    path = audit_log_path(home)
    ts = time.strftime(_FORMAT, time.localtime(now if now is not None else time.time()))
    record = {
        "ts": ts,
        "event": event,       # blocked | cancelled | guarded_passed
        "decision": decision, # forbidden | guarded | safe
        "argv": list(argv),
        "detail": detail,
    }
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return path