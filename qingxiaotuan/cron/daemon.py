"""Cron 常驻守护进程 —— 独立进程运行, 由 `qxt cron start --detach` 拉起。

用法:
    python -m qingxiaotuan.cron.daemon --check 60 --workspace <dir> [--home <dir>]

设计:
- 复用 `run_due_jobs` (与 `qxt cron tick` 同一执行路径), 行为一致;
- 信号处理: SIGTERM/SIGINT 优雅退出 (分片睡眠, 及时响应);
- 心跳: 周期性刷新 ~/.qingxiaotuan/cron/daemon.heartbeat, 供 `qxt cron status` 判断存活;
- 任何任务异常都在 run_due_jobs 内被捕获, 守护循环绝不因单个任务崩溃。
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from ..app import build_kernel
from .runner import run_due_jobs

log = logging.getLogger("qingxiaotuan.cron.daemon")

_stop = threading.Event()


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    log.info("收到信号 %s, 正在优雅退出", signum)
    _stop.set()


def _heartbeat_path(home: Path) -> Path:
    return home / "cron" / "daemon.heartbeat"


def _touch_heartbeat(home: Path) -> None:
    try:
        _heartbeat_path(home).write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:
        log.warning("心跳写入失败: %s", exc)


def _sleep_slices(seconds: int) -> None:
    """分片睡眠, 每 1 秒检查一次停止标志, 保证信号能被及时响应。"""
    for _ in range(max(1, int(seconds))):
        if _stop.is_set():
            return
        time.sleep(1)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="qxt-cron-daemon")
    parser.add_argument("--check", type=int, default=60, help="检查间隔 (秒)")
    parser.add_argument("--workspace", default=".", help="任务工作区目录")
    parser.add_argument("--home", default=None, help="QXT 家目录 (默认取配置)")
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    kernel = build_kernel()
    config = kernel.require("config")
    home = Path(args.home) if args.home else config.home
    workspace = str(Path(args.workspace).resolve())
    check = max(1, int(args.check))

    log.info("cron 守护启动: workspace=%s check=%ds", workspace, check)
    _touch_heartbeat(home)
    while not _stop.is_set():
        try:
            run_due_jobs(kernel, config, workspace, home, stream=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("cron 守护循环异常: %s", exc)
        _touch_heartbeat(home)
        _sleep_slices(check)
    log.info("cron 守护退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
