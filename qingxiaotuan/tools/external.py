"""外部能力引擎集成 - 纯 Python 引擎

引擎通过进程内直调与内核对话 (零 IPC 开销)。
"""
import json
import logging
import os
from typing import Any, Dict, Optional, cast

from ..core.kernel import Kernel, Plugin
from ..ext.registry import get_engine, ENGINE_MAP
from .base import Tool, ToolContext

# 引擎实例缓存: 同一引擎只实例化一次, 避免重复创建
_ENGINE_INSTANCES: Dict[str, Any] = {}

logger = logging.getLogger(__name__)

# 索引缓存: ext_index_build 的结果供 ext_index_query 复用 (进程内)
_INDEX_CACHE: Dict[str, Any] = {}


def _maybe_json(value: Any) -> Any:
    """若传入的是 JSON 字符串, 解析为对象 (工具参数常以字符串形式携带 JSON)。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _fmt(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=2)


def _get_engine(name: str) -> Any:
    """获取或缓存引擎实例 (进程内直调, 零 IPC 开销)。"""
    if name not in _ENGINE_INSTANCES:
        _ENGINE_INSTANCES[name] = get_engine(name)
    return _ENGINE_INSTANCES[name]


def _call(engine: str, method: str, params: dict, timeout: int = 30) -> Any:
    """进程内直调引擎方法, 返回结构化结果; 失败返回 {"error": ...} 而非抛异常。

    兼容两种引擎接口:
    1. methods 字典模式 (safety/crypto/diff/ansi/index/json/search/notify): instance.methods[method](params)
    2. handle(line) IPC 协议模式 (rules/skill-market): 通过 JSONL 协议转发
    """
    try:
        instance = _get_engine(engine)
        # 模式1: methods 字典 (推荐, 最高效)
        methods_dict = getattr(instance, 'methods', None)
        if isinstance(methods_dict, dict):
            handler = methods_dict.get(method)
            if handler is None:
                return {"error": f"引擎 {engine} 未知方法: {method}"}
            return handler(params)
        # 模式2: handle(line) IPC 协议兼容 (rules/skill-market)
        handle_fn = getattr(instance, 'handle', None)
        if callable(handle_fn):
            import json as _json
            req = {"id": 1, "method": method, "params": params}
            resp_str = handle_fn(_json.dumps(req, ensure_ascii=False))
            resp = _json.loads(resp_str)
            if resp.get("ok"):
                return resp.get("result")
            else:
                return {"error": resp.get("error", f"引擎 {engine} 方法 {method} 失败")}
        return {"error": f"引擎 {engine} 无可用接口 (无 methods 字典或 handle 方法)"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _diff(ctx, old: str, new: str) -> str:
    res = _call("diff", "diff", {"old": old, "new": new})
    if isinstance(res, dict) and "diff" in res:
        return cast(str, res["diff"])
    return _fmt(res)


def _patch(ctx, patch: str, content: str) -> str:
    return _fmt(_call("diff", "patch", {"source": content, "patch": patch}))


def _merge3(ctx, base: str, a: str, b: str) -> str:
    return _fmt(_call("diff", "merge3", {"base": base, "ours": a, "theirs": b}))


def _crypto_seal(ctx, plaintext: str, password: str, salt: str = "") -> str:
    res = _call("crypto", "seal", {"passphrase": password, "plaintext": plaintext})
    if isinstance(res, dict) and "ciphertext_b64" in res:
        blob = json.dumps({
            "ciphertext_b64": res["ciphertext_b64"],
            "iv_b64": res["iv_b64"],
            "mac_b64": res["mac_b64"],
            "iterations": res.get("iterations", 100000),
        }, ensure_ascii=False)
        return _fmt({"blob": blob, "salt_b64": res["salt_b64"]})
    return _fmt(res)


def _crypto_unseal(ctx, blob: str, salt: str, password: str) -> str:
    try:
        data = json.loads(blob) if isinstance(blob, str) else blob
    except json.JSONDecodeError:
        return "[错误] blob 不是合法 JSON"
    res = _call("crypto", "open", {
        "passphrase": password,
        "salt_b64": salt,
        "ciphertext_b64": data.get("ciphertext_b64", ""),
        "iv_b64": data.get("iv_b64", ""),
        "mac_b64": data.get("mac_b64", ""),
        "iterations": data.get("iterations", 100000),
    })
    return _fmt(res)


def _safety_score(ctx, command: str) -> str:
    return _fmt(_call("safety", "score", {"command": command}))


def _ansi_strip(ctx, text: str) -> str:
    res = _call("ansi", "strip", {"text": text})
    if isinstance(res, dict) and "text" in res:
        return cast(str, res["text"])
    return _fmt(res)


def _index_build(ctx, root: str) -> str:
    paths = []
    if os.path.isdir(root):
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.endswith((".py", ".js", ".ts", ".c", ".h", ".java", ".go", ".rs")):
                    paths.append(os.path.join(dirpath, fn))
    elif os.path.isfile(root):
        paths = [root]
    res = _call("index", "index", {"paths": paths})
    if isinstance(res, dict) and "indexed" in res:
        symbols = sorted({s for item in res["indexed"] for s in item.get("symbols", [])})
        _INDEX_CACHE["data"] = res["indexed"]
        return _fmt({"files": [item["path"] for item in res["indexed"]],
                     "symbols": symbols, "ok": True, "count": len(res["indexed"])})
    return _fmt(res)


def _index_query(ctx, symbol: str) -> str:
    res = _call("index", "search", {"query": symbol, "index": _INDEX_CACHE.get("data", [])})
    return _fmt(res)


def _rules_load(ctx, rules_yaml: str = "", rules_path: str = "") -> str:
    params = {}
    if rules_yaml:
        params["rules_yaml"] = rules_yaml
    elif rules_path:
        params["rules_path"] = rules_path
    else:
        return "[错误] 需提供 rules_yaml 或 rules_path"
    return _fmt(_call("rules", "load", params))


def _rules_check(ctx, path: str, content: str) -> str:
    return _fmt(_call("rules", "check", {"path": path, "content": content}))


def _skill_search(ctx, query: str) -> str:
    return _fmt(_call("skill-market", "search", {"query": query}))


def _json_pointer(ctx, doc: Any, pointer: str) -> str:
    return _fmt(_call("json", "pointer_get", {"doc": _maybe_json(doc), "pointer": pointer}))


def _json_diff(ctx, base: Any, overlay: Any) -> str:
    return _fmt(_call("json", "diff", {"base": _maybe_json(base), "overlay": _maybe_json(overlay)}))


def _json_merge(ctx, base: Any, overlay: Any) -> str:
    return _fmt(_call("json", "merge", {"base": _maybe_json(base), "overlay": _maybe_json(overlay)}))


def _search(ctx, root: str, pattern: str, max_results: int = 100) -> str:
    return _fmt(_call("search", "search", {"root": root, "pattern": pattern, "max_results": max_results}))


def _notify(ctx, title: str, message: str) -> str:
    return _fmt(_call("notify", "notify", {"title": title, "message": message}))


def safety_check(ctx: ToolContext, command: str) -> dict:
    """结构化安全评分 (供内核服务调用): 返回 dict 而非字符串。
    失败时返回低风险占位, 不阻断正常命令执行。"""
    try:
        raw = _call("safety", "score", {"command": command})
        if isinstance(raw, dict) and "risk" in raw:
            return {
                "risk": raw.get("risk", "none"),
                "block": bool(raw.get("block", False)),
                "reasons": raw.get("reasons") or [],
                "safe_preview": raw.get("suggestions") or [],
            }
        return {"risk": "none", "block": False, "unavailable": True,
                "reasons": [str(raw)], "safe_preview": []}
    except Exception as exc:  # noqa: BLE001
        return {"risk": "none", "block": False, "unavailable": True,
                "reasons": [f"安全引擎调用失败: {type(exc).__name__}: {exc}"],
                "safe_preview": []}


class _SafetyService:
    """内核服务包装: shell.py 通过 kernel.require('safety_check') 调用。"""

    def check(self, ctx: ToolContext, command: str) -> dict:
        return safety_check(ctx, command)


class ExternalToolsPlugin(Plugin):
    """注册外部引擎工具到工具注册表"""

    name = "tools.external"
    provides = ["safety_check"]
    requires = ["tool_registry"]

    def activate(self, kernel) -> None:
        registry = kernel.require("tool_registry")
        kernel.provide("safety_check", _SafetyService(), owner=self.name)

        tools = [
            Tool(
                name="ext_diff",
                description="行级/词级 diff (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "old": {"type": "string", "description": "原始文本"},
                        "new": {"type": "string", "description": "新文本"},
                    },
                    "required": ["old", "new"],
                },
                handler=_diff,
            ),
            Tool(
                name="ext_patch",
                description="应用 unified diff patch (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "patch": {"type": "string", "description": "unified diff 内容"},
                        "content": {"type": "string", "description": "被补丁的原始文本"},
                    },
                    "required": ["patch", "content"],
                },
                handler=_patch,
            ),
            Tool(
                name="ext_merge3",
                description="3-way merge (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "base": {"type": "string", "description": "基准文本"},
                        "a": {"type": "string", "description": "改动A"},
                        "b": {"type": "string", "description": "改动B"},
                    },
                    "required": ["base", "a", "b"],
                },
                handler=_merge3,
            ),
            Tool(
                name="ext_crypto_seal",
                description="口令加密 (PBKDF2 派生 + SHA256-keystream 流式加密), 返回 blob 与 salt_b64",
                parameters={
                    "type": "object",
                    "properties": {
                        "plaintext": {"type": "string", "description": "明文"},
                        "password": {"type": "string", "description": "口令"},
                        "salt": {"type": "string", "description": "盐值(base64, 可选)"},
                    },
                    "required": ["plaintext", "password"],
                },
                handler=_crypto_seal,
            ),
            Tool(
                name="ext_crypto_unseal",
                description="口令解密 (对应 ext_crypto_seal), 需 blob/salt/口令",
                parameters={
                    "type": "object",
                    "properties": {
                        "blob": {"type": "string", "description": "密文 blob"},
                        "salt": {"type": "string", "description": "盐值(base64)"},
                        "password": {"type": "string", "description": "口令"},
                    },
                    "required": ["blob", "salt", "password"],
                },
                handler=_crypto_unseal,
            ),
            Tool(
                name="ext_safety_score",
                description="安全风险评分: 命令/SQL 静态分析 (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要评估的命令文本"},
                    },
                    "required": ["command"],
                },
                handler=_safety_score,
            ),
            Tool(
                name="ext_ansi_strip",
                description="剥离 ANSI 转义序列 (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "含 ANSI 转义的文本"},
                    },
                    "required": ["text"],
                },
                handler=_ansi_strip,
            ),
            Tool(
                name="ext_index_build",
                description="索引一个目录的符号 (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "root": {"type": "string", "description": "要索引的根目录"},
                    },
                    "required": ["root"],
                },
                handler=_index_build,
            ),
            Tool(
                name="ext_index_query",
                description="查询已索引的符号定义位置 (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "符号名"},
                    },
                    "required": ["symbol"],
                },
                handler=_index_query,
            ),
            Tool(
                name="ext_rules_load",
                description="向规则引擎载入 YAML 规则 (rules_yaml 或 rules_path 二选一)",
                parameters={
                    "type": "object",
                    "properties": {
                        "rules_yaml": {"type": "string", "description": "规则 YAML 文本"},
                        "rules_path": {"type": "string", "description": "规则文件路径"},
                    },
                    "required": [],
                },
                handler=_rules_load,
            ),
            Tool(
                name="ext_rules_check",
                description="用规则引擎对单个文件做策略校验 (需先 load 规则)",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "文件内容"},
                    },
                    "required": ["path", "content"],
                },
                handler=_rules_check,
            ),
            Tool(
                name="ext_skill_search",
                description="在技能市场 registry 里搜索技能",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "查询词"},
                    },
                    "required": ["query"],
                },
                handler=_skill_search,
            ),
            Tool(
                name="ext_json_pointer",
                description="RFC 6901 JSON Pointer 取值 (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "doc": {"type": "object", "description": "JSON 文档"},
                        "pointer": {"type": "string", "description": "JSON Pointer 路径"},
                    },
                    "required": ["doc", "pointer"],
                },
                handler=_json_pointer,
            ),
            Tool(
                name="ext_json_diff",
                description="逐路径 JSON diff (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "base": {"type": "object", "description": "基准 JSON"},
                        "overlay": {"type": "object", "description": "覆盖 JSON"},
                    },
                    "required": ["base", "overlay"],
                },
                handler=_json_diff,
            ),
            Tool(
                name="ext_json_merge",
                description="深合并 JSON (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "base": {"type": "object", "description": "基准 JSON"},
                        "overlay": {"type": "object", "description": "覆盖 JSON"},
                    },
                    "required": ["base", "overlay"],
                },
                handler=_json_merge,
            ),
            Tool(
                name="ext_search",
                description="递归正则搜索文件 (纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "root": {"type": "string", "description": "根目录"},
                        "pattern": {"type": "string", "description": "正则表达式"},
                        "max_results": {"type": "integer", "description": "最大结果数", "default": 100},
                    },
                    "required": ["root", "pattern"],
                },
                handler=_search,
            ),
            Tool(
                name="ext_notify",
                description="桌面通知 (跨平台, 纯 Python)",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "通知标题"},
                        "message": {"type": "string", "description": "通知内容"},
                    },
                    "required": ["title", "message"],
                },
                handler=_notify,
            ),
        ]

        for tool in tools:
            registry.register(tool)
        logger.info("External engine tools registered: %s", [t.name for t in tools])
