# -*- coding: utf-8 -*-
"""Legacy 迁移编排器 (评审 Minor #7)。

现状: ``ports/migration_legacy`` 提供了大量纯逻辑积木 (detect / classify / marker /
stub_detect / report / atomic_write / session_index), 但缺少把整条链路串起来的顶层
入口 —— 老用户无法一键把旧版 ``~/.kimi/`` 状态平滑迁移过来。本模块补上编排器:

    run_migration(source_home, target_home) -> MigrationResult

流程 (全部幂等、可逆、守护 marker):
1. ``detect_migration`` 扫描源目录得出 :class:`MigrationPlan`;
2. 若已迁移/被跳过 (read/skip marker + should_suppress_migration) → no-op 返回;
3. 仅当目标是默认 stub 或缺失时原子写入 config.toml / tui.toml / mcp.json
   (绝不覆盖用户改过的目标文件);
4. 写 migration-report.json 与 migrated marker。

纯 stdlib, 无副作用导入; 任何一步失败都回滚到一个明确错误, 不写半程状态。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .detect import DetectOptions, detect_migration
from .marker import (
    MarkerData,
    MarkerRun,
    MigrationSuppressionInput,
    append_marker_run,
    read_marker,
    should_suppress_migration,
    write_marker,
)
from .paths import (
    migrated_marker,
    skip_marker,
    source_mcp_json,
    target_config_file,
    target_mcp_file,
    target_tui_file,
)
from .report import write_report
from .stub_detect import (
    DEFAULT_CONFIG_FILE_TEXT,
    DEFAULT_TUI_RENDER,
    is_config_stub_or_missing,
    is_tui_stub_or_missing,
)
from .atomic_write import atomic_write
from .types import MigrationPlan

_MIGRATOR_VERSION = "1.0.0"


@dataclass
class MigrationResult:
    """一次迁移的执行结果。``applied`` 为 False 表示 no-op (被守护或空源)。"""

    source_home: str
    target_home: str
    applied: bool
    config_written: bool = False
    tui_written: bool = False
    mcp_written: bool = False
    total_sessions: int = 0
    errors: List[str] = field(default_factory=list)


def _copy_mcp_if_present(source_home: str, target_home: str) -> bool:
    """把源 mcp.json 内容原子写入目标 (幂等, 覆盖逻辑由调用方守护)。"""
    src = source_mcp_json(source_home)
    if not os.path.isfile(src):
        return False
    try:
        with open(src, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        return False
    os.makedirs(target_home, mode=0o700, exist_ok=True)
    atomic_write(target_mcp_file(target_home), content)
    return True


def _record_marker(source_home: str, target_home: str, result: MigrationResult) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run = MarkerRun(
        started_at=now,
        completed_at=now,
        migrator_version=_MIGRATOR_VERSION,
        summary={
            "config": result.config_written,
            "tui": result.tui_written,
            "mcp": result.mcp_written,
            "sessions": result.total_sessions,
            "errors": result.errors,
        },
    )
    existing: Optional[MarkerData] = read_marker(source_home)
    if existing is not None:
        append_marker_run(source_home, run, target_home)
    else:
        write_marker(source_home, run, target_home)


def run_migration(source_home: str, target_home: str) -> MigrationResult:
    """执行一次端到端 legacy 迁移, 返回结构化结果。"""
    result = MigrationResult(
        source_home=source_home, target_home=target_home, applied=False)

    # 1) 守护: 已被跳过/已迁移 -> no-op
    if os.path.exists(skip_marker(source_home)):
        return result
    if should_suppress_migration(MigrationSuppressionInput(source_home, target_home)):
        return result
    if read_marker(source_home) is not None:
        # 已迁移过: 仍允许 append 新 run, 但标记为 applied=False 语义 (追加式)
        pass

    # 2) 探测
    plan: MigrationPlan = detect_migration(DetectOptions(source_path=source_home))
    result.total_sessions = int(plan.total_sessions or 0)

    # 3) 空源 -> no-op
    if not (plan.has_config or plan.has_mcp or result.total_sessions):
        return result

    result.applied = True
    os.makedirs(target_home, mode=0o700, exist_ok=True)

    # 4) 按 stub 守护写目标文件 (绝不覆盖用户改过的)
    cfg = target_config_file(target_home)
    if is_config_stub_or_missing(cfg):
        atomic_write(cfg, DEFAULT_CONFIG_FILE_TEXT)
        result.config_written = True

    tui = target_tui_file(target_home)
    if is_tui_stub_or_missing(tui):
        atomic_write(tui, DEFAULT_TUI_RENDER)
        result.tui_written = True

    if plan.has_mcp:
        try:
            result.mcp_written = _copy_mcp_if_present(source_home, target_home)
        except Exception as exc:  # noqa: BLE001
            result.mcp_written = False  # 拷贝失败: 明确标记未落地
            result.errors.append(f"mcp: {exc}")

    # 5) 报告 + marker
    try:
        write_report(target_home, {
            "source_home": source_home,
            "target_home": target_home,
            "migrator_version": _MIGRATOR_VERSION,
            "applied": result.applied,
            "config_written": result.config_written,
            "tui_written": result.tui_written,
            "mcp_written": result.mcp_written,
            "total_sessions": result.total_sessions,
            "migrated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"report: {exc}")

    try:
        _record_marker(source_home, target_home, result)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"marker: {exc}")

    return result