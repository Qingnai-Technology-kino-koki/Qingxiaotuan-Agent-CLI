"""qxt ext —— 外部能力引擎 (纯 Python) 的命令行入口。

把纯 Python 引擎统一成一套可被人直接调用的子命令,
方便在不启动完整 Agent 的情况下调试 / 验证外部引擎:

    qxt ext engines                列出环境中真正可用的引擎
    qxt ext call <引擎> <方法> [JSON参数]   直接调用某个引擎的方法
    qxt ext selftest [引擎...]     逐个启动引擎, 跑 list/ping, 报告健康度
    qxt ext info <引擎>            显示某引擎的元信息 (方法列表 / 版本)

引擎由 qingxiaotuan.core.ipc_client.ExternalEngineManager 负责发现与驱动。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.table import Table

from ..core.ipc_client import ExternalEngineManager, IpcError

console = Console()

# 引擎分类: 用于 `ext engines` 的分组展示 (现在全是 Python)
_ENGINE_GROUPS: Dict[str, str] = {
    "diff": "Python", "patch": "Python", "merge3": "Python",
    "crypto": "Python", "index": "Python", "ansi": "Python",
    "safety": "Python", "json": "Python", "search": "Python",
    "notify": "Python",
}


def _build_manager(config: Optional[dict] = None) -> ExternalEngineManager:
    return ExternalEngineManager(config=config or {}, quiet=True)


def _load_config_from_profile(profile: str, patch_file: Optional[str]) -> dict:
    try:
        from ..config.loader import Config as _Cfg
        cfg = _Cfg(profile=profile, patch_file=patch_file)
        raw = cfg.raw() or {}
        ext = raw.get("ext", {}) or {}
        mapped: Dict[str, Any] = {}
        if ext.get("node_exe"):
            mapped["ext.node_exe"] = ext["node_exe"]
        if ext.get("c_bin_dir"):
            mapped["ext.c_bin_dir"] = ext["c_bin_dir"]
        if ext.get("ts_src_dir"):
            mapped["ext.ts_src_dir"] = ext["ts_src_dir"]
        if ext.get("ts_dist_dir"):
            mapped["ext.ts_dist_dir"] = ext["ts_dist_dir"]
        if ext.get("repo_root"):
            mapped["ext.repo_root"] = ext["repo_root"]
        return mapped
    except Exception:  # noqa: BLE001
        return {}


def cmd_ext(args) -> int:
    sub = getattr(args, "ext_cmd", None)
    if sub == "engines":
        return _ext_engines(args)
    if sub == "call":
        return _ext_call(args)
    if sub == "selftest":
        return _ext_selftest(args)
    if sub == "info":
        return _ext_info(args)
    console.print("[red]未知 ext 子命令[/]")
    return 2


def _ext_engines(args) -> int:
    cfg = _load_config_from_profile(args.profile, args.patch)
    mgr = _build_manager(cfg)
    avail = mgr.available()
    if not avail:
        console.print("[yellow]未发现任何可用引擎。[/]")
        return 0
    from collections import defaultdict
    by_group: Dict[str, List[str]] = defaultdict(list)
    for name in avail:
        by_group[_ENGINE_GROUPS.get(name, "Python")].append(name)
    table = Table(title="可用的外部能力引擎", show_lines=False)
    table.add_column("类型", style="bold")
    table.add_column("引擎", style="cyan")
    table.add_column("启动命令", style="dim")
    for grp in ("Python",):
        names = by_group.get(grp, [])
        if not names:
            continue
        for name in names:
            cmd = mgr.command_for(name)
            table.add_row(grp, name, " ".join(cmd) if cmd else "-")
    console.print(table)
    console.print(f"[dim]共 {len(avail)} 个可用。[/]")
    return 0


def _ext_call(args) -> int:
    engine = args.engine
    method = args.method
    raw = args.params or "{}"
    try:
        params = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"[red]参数不是合法 JSON: {exc}[/]")
        return 2
    if not isinstance(params, dict):
        console.print("[red]参数必须是 JSON 对象 (key/value)[/]")
        return 2

    cfg = _load_config_from_profile(args.profile, args.patch)
    mgr = _build_manager(cfg)
    if not mgr.command_for(engine):
        console.print(f"[red]引擎不可用: {engine}[/]")
        return 1
    try:
        result = mgr.call(engine, method, params, timeout=args.timeout)
    except IpcError as exc:
        console.print(f"[red]调用失败: {exc}[/]")
        return 1
    finally:
        mgr.close_all()
    if isinstance(result, (dict, list)):
        console.print_json(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        console.print(result)
    return 0


def _fetch_methods(mgr: ExternalEngineManager, engine: str, timeout: float):
    for meth in ("_meta", "list"):
        try:
            res = mgr.call(engine, meth, timeout=timeout)
        except IpcError as exc:
            if "unknown method" in str(exc) or "未知方法" in str(exc):
                continue
            return (None, str(exc))
        if isinstance(res, dict) and "methods" in res:
            m = res["methods"]
            if isinstance(m, list):
                return ([x.get("name", x) if isinstance(x, dict) else x for x in m], None)
        if isinstance(res, list):
            return (res, None)
    return (None, "无 list/_meta 方法")


def _ext_selftest(args) -> int:
    cfg = _load_config_from_profile(args.profile, args.patch)
    mgr = _build_manager(cfg)
    targets = list(args.engines) if getattr(args, "engines", None) else mgr.available()
    if not targets:
        console.print("[yellow]没有可测试的引擎。[/]")
        return 0
    table = Table(title="外部引擎自检", show_lines=False)
    table.add_column("引擎", style="cyan")
    table.add_column("类型", style="bold")
    table.add_column("就绪", style="green")
    table.add_column("方法数", style="magenta")
    table.add_column("备注", style="dim")
    failures = 0
    for engine in targets:
        grp = _ENGINE_GROUPS.get(engine, "Python")
        cmd = mgr.command_for(engine)
        if not cmd:
            table.add_row(engine, grp, "[red]缺失[/]", "-", "未安装")
            failures += 1
            continue
        methods, err = _fetch_methods(mgr, engine, args.timeout)
        if err is not None:
            table.add_row(engine, grp, "[red]✗[/]", "-", err[:60])
            failures += 1
        else:
            n = len(methods) if methods is not None else "?"
            table.add_row(engine, grp, "[green]✓[/]", str(n), "")
    console.print(table)
    mgr.close_all()
    if failures:
        console.print(f"[yellow]{failures} 个引擎不可用。[/]")
        return 1
    console.print("[green]所有引擎就绪。[/]")
    return 0


def _ext_info(args) -> int:
    engine = args.engine
    cfg = _load_config_from_profile(args.profile, args.patch)
    mgr = _build_manager(cfg)
    cmd = mgr.command_for(engine)
    if not cmd:
        console.print(f"[red]引擎不可用: {engine}[/]")
        return 1
    console.print(f"[cyan]{engine}[/] [dim]({_ENGINE_GROUPS.get(engine, 'Python')})[/]")
    console.print(f"[dim]启动命令:[/] {' '.join(cmd)}")
    meta = None
    try:
        meta = mgr.call(engine, "_meta", timeout=args.timeout)
    except IpcError:
        meta = None
    if isinstance(meta, dict):
        console.print(f"[dim]版本:[/] {meta.get('version', '-')}")
        methods = meta.get("methods", [])
        if methods:
            console.print(f"[dim]方法:[/] {', '.join(methods)}")
    mgr.close_all()
    return 0
