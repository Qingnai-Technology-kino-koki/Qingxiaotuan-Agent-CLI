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
from typing import Dict, List, Optional

from rich.table import Table

from ..core.ipc_client import ExternalEngineManager, IpcError
from ..ui.plain_console import console

# 引擎分类: 用于 `ext engines` 的分组展示 (现在全是 Python)
_ENGINE_GROUPS: Dict[str, str] = {
    "diff": "Python", "patch": "Python", "merge3": "Python",
    "crypto": "Python", "index": "Python", "ansi": "Python",
    "safety": "Python", "json": "Python", "search": "Python",
    "notify": "Python", "rules": "Python", "skill-market": "Python",
}


def _load_config_from_profile(profile: str = "default", patch_file: Optional[str] = None) -> dict:
    """加载指定 profile 的合并配置视图 (供 ext 引擎使用)。"""
    from ..config import Config, load_dotenv
    load_dotenv()
    return Config(profile=profile, patch_file=patch_file).data


def _build_manager(config: Optional[dict] = None) -> ExternalEngineManager:
    return ExternalEngineManager(config=config or {})


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
    console.print("未知 ext 子命令")
    return 2


def _ext_engines(args) -> int:
    mgr = _build_manager()
    avail = mgr.list_engines()
    if not avail:
        console.print("未发现任何可用引擎。")
        return 0
    from collections import defaultdict
    by_group: Dict[str, List[str]] = defaultdict(list)
    for name in avail:
        by_group[_ENGINE_GROUPS.get(name, "Python")].append(name)
    table = Table(title="可用的外部能力引擎", show_lines=False)
    table.add_column("类型")
    table.add_column("引擎")
    for grp in ("Python",):
        names = by_group.get(grp, [])
        if not names:
            continue
        for name in names:
            table.add_row(grp, name)
    console.print(table)
    console.print(f"共 {len(avail)} 个可用。")
    return 0


def _ext_call(args) -> int:
    engine = args.engine
    method = args.method
    raw = args.params or "{}"
    try:
        params = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"参数不是合法 JSON: {exc}")
        return 2
    if not isinstance(params, dict):
        console.print("参数必须是 JSON 对象 (key/value)")
        return 2

    mgr = _build_manager()
    if engine not in mgr.list_engines():
        console.print(f"引擎不可用: {engine}")
        return 1
    try:
        result = mgr.call(engine, method, params, timeout=args.timeout)
    except IpcError as exc:
        console.print(f"调用失败: {exc}")
        return 1
    finally:
        mgr.close_all()
    if isinstance(result, (dict, list)):
        console.print_json(json.dumps(result, ensure_ascii=False, indent=2), highlight=False)
    else:
        console.print(result)
    return 0


def _fetch_methods(mgr: ExternalEngineManager, engine: str, timeout: float):
    try:
        res = mgr.call(engine, "_meta/list", timeout=timeout)
    except IpcError as exc:
        return (None, str(exc))
    if isinstance(res, dict) and "methods" in res:
        m = res["methods"]
        if isinstance(m, list):
            return ([x.get("name", x) if isinstance(x, dict) else x for x in m], None)
    return (None, "无 _meta/list 方法")


def _ext_selftest(args) -> int:
    mgr = _build_manager()
    targets = list(args.engines) if getattr(args, "engines", None) else mgr.list_engines()
    if not targets:
        console.print("没有可测试的引擎。")
        return 0
    table = Table(title="外部引擎自检", show_lines=False)
    table.add_column("引擎")
    table.add_column("类型")
    table.add_column("就绪")
    table.add_column("方法数")
    table.add_column("备注")
    failures = 0
    for engine in targets:
        grp = _ENGINE_GROUPS.get(engine, "Python")
        methods, err = _fetch_methods(mgr, engine, args.timeout)
        if err is not None:
            table.add_row(engine, grp, "✗", "-", err[:60])
            failures += 1
        else:
            n = len(methods) if methods is not None else "?"
            table.add_row(engine, grp, "✓", str(n), "")
    console.print(table)
    mgr.close_all()
    if failures:
        console.print(f"{failures} 个引擎不可用。")
        return 1
    console.print("所有引擎就绪。")
    return 0


def _ext_info(args) -> int:
    engine = args.engine
    mgr = _build_manager()
    if engine not in mgr.list_engines():
        console.print(f"引擎不可用: {engine}")
        return 1
    console.print(f"{engine} ({_ENGINE_GROUPS.get(engine, 'Python')})")
    meta = None
    try:
        meta = mgr.call(engine, "_meta/list", timeout=args.timeout)
    except IpcError:
        meta = None
    if isinstance(meta, dict):
        console.print(f"版本: {meta.get('version', '-')}")
        methods = meta.get("methods", [])
        if methods:
            console.print(f"方法: {', '.join(methods)}")
    mgr.close_all()
    return 0
