"""安全策略更新提醒 —— 月度黑名单更新元数据维护。

注意: 本模块只负责「安全策略月度更新」的检查与记录。

白名单 (Trae 模式) / 多阶段确认 / 风险分级等能力统一收敛在 core/whitelist.py 与
ext/safety_engine.py, 请勿在本模块重复实现。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def check_monthly_update(config_dir: Optional[str] = None) -> Dict[str, Any]:
    """检查是否需要月度黑名单更新。

    每月第一个工作日提醒更新黑名单。
    返回: {"due": bool, "days_since_last": int, "message": str}
    """
    config_dir = config_dir or os.environ.get("QXT_HOME", "")
    if not config_dir:
        config_dir = os.path.join(str(Path.home()), ".qingxiaotuan")

    meta_path = os.path.join(config_dir, "security", "last_update.json")

    try:
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                last_update = data.get("last_update", "")
        else:
            last_update = ""
    except (json.JSONDecodeError, OSError):
        last_update = ""

    # 如果没有记录或超过30天，提醒更新
    if not last_update:
        return {"due": True, "days_since_last": 999, "message": "尚未记录安全策略更新时间，请立即更新黑名单"}

    try:
        last = datetime.fromisoformat(last_update)
        now = datetime.now()
        days = (now - last).days
        if days >= 30:
            return {"due": True, "days_since_last": days, "message": f"距离上次安全策略更新已 {days} 天，请更新黑名单"}
        return {"due": False, "days_since_last": days, "message": f"安全策略已更新 ({days} 天前)"}
    except ValueError:
        return {"due": True, "days_since_last": 999, "message": "安全策略更新时间格式异常，请重新记录"}


def record_monthly_update(config_dir: Optional[str] = None) -> None:
    """记录月度黑名单更新时间"""
    config_dir = config_dir or os.environ.get("QXT_HOME", "")
    if not config_dir:
        config_dir = os.path.join(str(Path.home()), ".qingxiaotuan")

    meta_path = os.path.join(config_dir, "security", "last_update.json")
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"last_update": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
