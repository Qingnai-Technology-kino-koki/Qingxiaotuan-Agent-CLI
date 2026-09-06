"""联网/搜索配置管理命令 —— cmd_network。

`qxt network configuration` / `qxt net con` 查看、设定并复位搜索基础设施参数
(搜索上限、默认条数、摘要截断、相关度 top_k、磁盘缓存、引擎顺序、超时等),
全部落在内置 `network` 配置段, 由 web 插件在运行时读取生效。

命令形态 (configuration / con / config 三个子命令别名等价):
    qxt network configuration            # 列出当前网络配置概览 (=list)
    qxt net con search_top_k             # 简写 get: 读取单项
    qxt net con describe                 # 全参数说明 + 当前值 + 默认值
    qxt net con set <key> <value>        # 写入 (自动类型/范围校验, JSON 值可解析)
    qxt net con reset [key]              # 复位某 key (缺省=全部) 到内置默认
    qxt net con dump                     # 打印整个 network 配置段

本模块不导入 app 链, 配置类命令保持毫秒级启动。
"""

from __future__ import annotations

import json
import re

from ..config import DEFAULT_CONFIG, Config, to_yaml_str
from ..ui.plain_console import console

_KNOWN_ACTIONS = {"list", "dump", "get", "set", "describe", "reset"}

# key -> (类型, 最小值, 最大值, 说明)。 类型: int/num/bool/str/list
_NET_META = {
    "search_max_results":        ("int", 1, 500,  "单次搜索最多可返回的网页数上限 (1~500)"),
    "search_default_results":    ("int", 1, None, "未指定 max_results 时的默认返回条数"),
    "search_top_k":              ("int", 0, None, "进入上下文的最相关条数, 0=不过滤 (采多注精→省 token)"),
    "search_snippet_max_chars":  ("int", 1, None, "每条摘要进入上下文前的最大字符数 (省 token)"),
    "search_max_pages":          ("int", 1, None, "分页请求的页数上限 (每页约 20 条)"),
    "search_cache":              ("bool", None, None, "是否启用搜索结果磁盘缓存 (重复查询省时省 token)"),
    "search_cache_ttl":          ("int", 0, None, "缓存有效期秒数, 0=能力范围内最久 (默认 6 小时)"),
    "search_cache_dir":          ("str", None, None, "缓存目录 (空串=禁用磁盘缓存)"),
    "search_engines":            ("list", None, None, "搜索引擎优先级顺序, 逗号/空格分隔 (失败自动切换)"),
    "fetch_max_chars":           ("int", 1, None, "web_fetch 抓取正文的最大字符数 (省 token)"),
    "fetch_timeout":             ("num", 1, None, "网络请求超时 (秒)"),
}

_ORDER = [
    "search_max_results", "search_default_results", "search_top_k",
    "search_snippet_max_chars", "search_max_pages", "search_cache",
    "search_cache_ttl", "search_cache_dir", "search_engines",
    "fetch_max_chars", "fetch_timeout",
]

_KNOWN_ENGINES = {"duckduckgo", "bing"}


def _coerce(key: str, kind: str, lo, hi, raw) -> tuple[object, str]:
    """按类型/范围把原始输入规整为合法值; 非法则返回 (None, 错误信息)。"""
    if kind == "bool":
        if isinstance(raw, bool):
            return raw, ""
        low = str(raw).strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True, ""
        if low in ("false", "0", "no", "off"):
            return False, ""
        return None, f"值应设为 true/false, 但现在给的是 {raw!r}"
    if kind == "list":
        items = (list(raw) if isinstance(raw, (list, tuple))
                 else [x for x in re.split(r"[,;\s]+", str(raw)) if x])
        unknown = [x for x in items if x not in _KNOWN_ENGINES]
        if unknown:
            return None, (f"未知引擎 {unknown}; 可选: {', '.join(sorted(_KNOWN_ENGINES))}")
        return items, ""
    if kind in ("int", "num"):
        try:
            v = int(raw) if kind == "int" else float(raw)
        except (TypeError, ValueError):
            return None, f"应设为一个{'整数' if kind == 'int' else '数字'}, 但现在给的是 {raw!r}"
        if lo is not None and v < lo:
            return None, f"{key} 不能小于 {lo}"
        if hi is not None and v > hi:
            return None, f"{key} 不能大于 {hi}"
        return v, ""
    # str
    return str(raw), ""


def _defaults() -> dict:
    return DEFAULT_CONFIG.get("network", {})


def cmd_network(args) -> int:
    """查看/设定/复位联网(搜索)配置。"""
    config = Config(profile=getattr(args, "profile", "default"),
                    patch_file=getattr(args, "patch", None))
    action = getattr(args, "action", None) or "list"
    key = getattr(args, "key", None)
    value = getattr(args, "value", None)

    # 简写: `qxt net con search_top_k` => get
    if action not in _KNOWN_ACTIONS:
        key, action = (key or action), "get"

    if action == "dump":
        console.print(to_yaml_str(config.get("network", {}) or {}))
    elif action == "describe":
        net = config.get("network", {}) or {}
        defaults = _defaults()
        console.print("联网/搜索配置 — 全部参数说明:")
        for k in _ORDER:
            kind, lo, hi, desc = _NET_META[k]
            cur = net.get(k, defaults.get(k))
            range_txt = ""
            if lo is not None and hi is not None:
                range_txt = f" [{lo}~{hi}]"
            elif lo is not None:
                range_txt = f" [>= {lo}]"
            console.print(f"  {k:<26s} = {cur!r}")
            console.print(f"      {desc}{range_txt}")
        console.print("\n改动用 `qxt net con set <key> <value>`, "
                      "复位用 `qxt net con reset [key]`。")
    elif action == "reset":
        if key:
            if key not in _NET_META:
                console.print(f"未知配置项: {key} (describe 可看全部)")
                return 1
            if config.delete_user(f"network.{key}"):
                console.print(f"network.{key} 已复位为默认")
            else:
                console.print(f"network.{key} 本就未自定义 (已是默认)")
        else:
            removed = [k for k in _NET_META
                       if config.delete_user(f"network.{k}")]
            if removed:
                console.print("已复位: " + ", ".join(f"network.{k}" for k in removed))
            else:
                console.print("network 配置全部为默认, 无需复位")
    elif action == "get":
        if not key:
            console.print("用法: qxt network configuration get <key>")
            return 1
        if key not in _NET_META:
            console.print("未知配置项, 用 `qxt net con describe` 查看全部参数。")
            return 1
        net = config.get("network", {}) or {}
        console.print(f"  {key} = {net.get(key, _defaults().get(key))}")
        console.print(f"     ({_NET_META[key][3]})")
    elif action == "set":
        if not key or value is None:
            console.print("用法: qxt network configuration set <key> <value>")
            return 1
        if key.startswith("network."):
            key = key[len("network."):]
        if key not in _NET_META:
            console.print(f"未知配置项: {key} (describe 可看全部)")
            return 1
        kind, lo, hi, _ = _NET_META[key]
        if isinstance(value, str):
            try:
                value = json.loads(value)  # 数字/布尔/列表由 JSON 规整
            except (json.JSONDecodeError, TypeError):
                pass
        coerced, err = _coerce(key, kind, lo, hi, value)
        if err:
            console.print(f"  {key}: {err}")
            return 1
        config.set_user(f"network.{key}", coerced)
        console.print(f"network.{key} = {coerced!r}")
    else:  # list
        net = config.get("network", {}) or {}
        defaults = _defaults()
        console.print("联网/搜索配置 (network):")
        for k in _ORDER:
            val = net.get(k, defaults.get(k))
            mark = "" if k in net else "  (默认)"
            console.print(f"  {k:<26s} {val!r}{mark}")
        console.print("\n提示: set 改值 / reset 复位 / describe 看说明; "
                      "默认值见 CONFIG.defaults.network")
    return 0