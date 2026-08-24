"""统一日志 —— 终端分级展示 + 文件落盘 (~/.qingxiaotuan/logs/qxt-YYYYMMDD.log)。

设计:
- 终端只显示 INFO 及以上, 且不带时间戳噪声 (rich 已着色);
- 文件记录 DEBUG 级, 含时间、级别、模块、消息, 便于事后排查;
- 支持请求/响应审计 (model.audit=debug 时打印完整 prompt/completion);
- 密钥类字段在日志中自动脱敏 (sk-... -> sk-***12)。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

_KEY_RE = re.compile(r"(sk-[A-Za-z0-9]{8})[A-Za-z0-9]+")


def redact(text: str) -> str:
    """脱敏日志中的密钥: sk-abc12345... -> sk-abc1***。"""
    if not text:
        return text
    return _KEY_RE.sub(lambda m: f"{m.group(1)}***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, dict):
            record.args = {k: redact(str(v)) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(redact(str(a)) for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def setup_logging(home: Path, level: int = logging.INFO, audit: bool = False) -> logging.Logger:
    """配置根 logger 与文件 handler, 返回名为 'qxt' 的 logger。"""
    logger = logging.getLogger("qxt")
    logger.setLevel(logging.DEBUG)  # 文件可记 DEBUG, 终端由 handler 控制
    logger.handlers.clear()
    logger.propagate = False

    # 终端 handler: 简洁, 仅级别+消息
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(_ConsoleFormatter())
    console.addFilter(RedactingFilter())
    logger.addHandler(console)

    # 文件 handler: 详细, 含时间/模块
    logs_dir = home / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"qxt-{datetime.now().strftime('%Y%m%d')}.log"
    try:
        file_h = logging.FileHandler(log_file, encoding="utf-8")
        file_h.setLevel(logging.DEBUG)
        file_h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(name)s.%(module)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        file_h.addFilter(RedactingFilter())
        logger.addHandler(file_h)
    except OSError:
        # 文件不可写时降级为仅终端
        pass

    logger.audit = audit  # type: ignore[attr-defined]
    return logger


class _ConsoleFormatter(logging.Formatter):
    """终端彩色: INFO 默认, WARN 黄, ERROR 红, DEBUG 暗灰。"""

    _COLORS = {
        "DEBUG": "dim",
        "INFO": "cyan",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold red",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelname, "cyan")
        msg = record.getMessage()
        if record.levelno <= logging.INFO:
            return f"[{color}]{msg}[/]" if False else msg  # rich 着色交由调用方
        return f"[{color}]{record.levelname}: {msg}[/]"


# 模块级便捷 logger (在 setup_logging 之前可用, 之后被替换)
log = logging.getLogger("qxt")
