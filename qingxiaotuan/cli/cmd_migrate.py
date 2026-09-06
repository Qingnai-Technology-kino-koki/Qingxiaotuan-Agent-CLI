# -*- coding: utf-8 -*-
"""qxt migrate —— 旧版 (~/.kimi) 配置/会话 → 新版 (~/.qingxiaotuan) 一键迁移。

把 ``ports/migration_legacy`` 的纯逻辑积木接到 CLI 入口:

    qxt migrate status                查看是否已迁移 (只读)
    qxt migrate detect --source <dir> 只读扫描旧版状态并汇报 (不写任何东西)
    qxt migrate run    --source <dir> [--target <dir>]  执行迁移 (幂等/可逆/守护)

所有行为 100% 复用编排器 :func:`run_migration` 的守护语义: 绝不覆盖用户改过的
目标文件、空源 no-op、已迁移/被跳过则不再执行。本模块轻量, 不导入 app 链。
"""

from __future__ import annotations

import os

from ..config import home_dir
from ..ports.migration_legacy import (
    DetectOptions,
    detect_migration,
    read_marker,
    run_migration,
)
from ..ports.migration_legacy.paths import (
    migration_errors_log_file,
    migration_report_file,
    migrated_marker,
    skip_marker,
    source_sessions_dir,
)
from ..ui.plain_console import console

# 默认源: 旧版 kimi-cli 主目录; 默认目标: 当前 qxt 主目录。
DEFAULT_LEGACY_SOURCE = os.path.expanduser("~/.kimi")


def _default_source() -> str:
    return DEFAULT_LEGACY_SOURCE


def _plan_description(plan) -> str:
    parts = []
    if plan.has_config:
        parts.append("config.toml")
    if plan.has_mcp:
        parts.append("mcp.json")
    if getattr(plan, "has_user_history", False):
        parts.append("user-history")
    n_sessions = int(getattr(plan, "total_sessions", 0) or 0)
    if n_sessions:
        parts.append(f"{n_sessions} 个会话")
    if getattr(plan, "detected_plugins", None):
        parts.append(f"{len(plan.detected_plugins)} 个插件")
    return "、".join(parts) if parts else "无 (空源)"


def _status_line(source: str) -> list[str]:
    lines = []
    if os.path.exists(skip_marker(source)):
        lines.append("  ! 已标记跳过迁移 (skip-marker 存在)")
    marker = read_marker(source)
    if marker is not None:
        lines.append(f"  ✓ 已迁移 (首次 {marker.first_migrated_at}, 最近 {marker.last_migrated_at}, "
                     f"共 {len(marker.runs)} 次)")
    else:
        lines.append("  · 未迁移")
    return lines


def cmd_migrate(args) -> int:
    """迁移编排器 CLI 入口。"""
    sub = getattr(args, "migrate_cmd", None)

    if sub == "status":
        source = getattr(args, "source", None) or _default_source()
        console.print(f"迁移状态  源: {source}")
        console.print(f"           目标: {home_dir()}")
        for line in _status_line(source):
            console.print(line)
        console.print(f"  源会话目录: {source_sessions_dir(source)}")
        return 0

    if sub == "detect":
        source = getattr(args, "source", None) or _default_source()
        if not os.path.exists(source):
            console.print(f"[detect] 源目录不存在: {source}")
            return 1
        plan = detect_migration(DetectOptions(source_path=source))
        console.print(f"检测结果  源: {source}")
        console.print(f"  配置: {'有' if plan.has_config else '无'}  "
                      f"MCP: {'有' if plan.has_mcp else '无'}  "
                      f"会话数: {int(plan.total_sessions or 0)}")
        console.print(f"  内容: {_plan_description(plan)}")
        for line in _status_line(source):
            console.print(line)
        return 0

    if sub == "run":
        source = getattr(args, "source", None) or _default_source()
        target = getattr(args, "target", None) or str(home_dir())
        console.print(f"迁移  源: {source}")
        console.print(f"      目标: {target}")
        if not os.path.exists(source):
            console.print("[migrate] 源目录不存在 (无需迁移): " + source)
            return 0
        try:
            result = run_migration(source, target)
        except Exception as exc:  # noqa: BLE001 - 迁移失败需明确报错
            console.print(f"[migrate] 迁移失败: {exc}")
            return 1
        if not result.applied:
            console.print("  no-op: 空源或已被跳过/迁移过 (未改动任何文件)")
            return 0
        console.print("  迁移完成:")
        console.print(f"    config.toml : {'写入' if result.config_written else '保留(非默认)'}")
        console.print(f"    tui.toml    : {'写入' if result.tui_written else '保留(非默认)'}")
        console.print(f"    mcp.json    : {'复制' if result.mcp_written else '无/跳过'}")
        console.print(f"    会话数      : {result.total_sessions}")
        for err in result.errors:
            console.print(f"    ! {err}")
        marker = read_marker(source)
        if marker is not None:
            console.print(f"    已记录迁移标记 ({marker.migrator_version})")
        console.print(f"  报告: {migration_report_file(target)}")
        if result.errors and os.path.exists(migration_errors_log_file(target)):
            console.print(f"  错误日志: {migration_errors_log_file(target)}")
        return 1 if result.errors else 0

    console.print("用法:")
    console.print("  qxt migrate status")
    console.print("  qxt migrate detect  [--source <旧版目录>]")
    console.print("  qxt migrate run     [--source <旧版目录>] [--target <新目录>]")
    return 2