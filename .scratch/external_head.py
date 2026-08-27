"""外部引擎工具插件 —— 把 ext/c (C 引擎) 与 ext/ts (TS 模块) 通过 JSONL IPC 接入内核。

每个工具在首次被调用时懒加载对应的引擎进程 (进程长驻, 复用连接),
因此一组相关的操作 (如 diff/patch/merge3 都跑 qxt_diff.exe) 共享同一个进程。

可用引擎 (自动探测, 没编译/没 node 的会被跳过):
    C:  diff / patch / merge3 / crypto(derive,seal,unseal,fingerprint,sign,verify) / index(build,query,stats)
        / ansi(strip,render) / sandbox / watch / safety(analyze,score) / json(pointer,diff,merge)
    TS: rules / plugin-host / mcp-client / skill-market / dashboard / agent-sdk
        / search (本地全文检索) / notify (桌面通知)
"""

from __future__ import annotations

import json
from typing import Any

from ..core.ipc_client import ExternalEngineManager, IpcError
from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop


def _fmt(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=2)


class ExternalToolsPlugin(Plugin):
    name = "tools.external"
    provides = ["safety_check"]   # 供 shell.py 在命令执行前复用同一 IPC 进程做拦截
    requires = ["tool_registry"]

    def __init__(self) -> None:
        super().__init__()
        self._mgr: ExternalEngineManager | None = None

    # ------------------------------------------------------------ 内部调用

    def _mgr_get(self, kernel: Kernel) -> ExternalEngineManager:
        if self._mgr is None:
            cfg = kernel.get("config") or {}
            # node / c-bin / ts-src 等可在 config.ext.* 覆盖
            self._mgr = ExternalEngineManager(dict(cfg))
        return self._mgr

    def _call(self, ctx: ToolContext, engine: str, method: str, params: dict, timeout: int = 30) -> str:
        mgr = self._mgr_get(ctx.kernel)
        try:
            return _fmt(mgr.call(engine, method, params, timeout=timeout))
        except IpcError as exc:
            return f"[外部引擎错误] {engine}.{method}: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"[外部引擎异常] {engine}.{method}: {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 工具实现

    def _diff(self, ctx, old: str, new: str) -> str:
        res = self._call(ctx, "diff", "diff", {"a": old, "b": new})
        # 把结构化 hunks 拍平为统一补丁文本, 便于 ext_patch 消费与人工审阅
        try:
            data = json.loads(res) if isinstance(res, str) else res
            lines: list = []
            for h in data.get("hunks", []):
                lines.extend(h.get("lines", []))
            return "\n".join(lines)
        except Exception:
            return res

    def _patch(self, ctx, patch: str, content: str) -> str:
        return self._call(ctx, "diff", "patch", {"text": content, "patch": patch})

    def _merge3(self, ctx, base: str, a: str, b: str) -> str:
        return self._call(ctx, "diff", "merge3", {"base": base, "ours": a, "theirs": b})

    def _crypto_derive(self, ctx, password: str) -> str:
        # derive 只生成并回传 salt_b64 (PBKDF2 在引擎内部完成)
        return self._call(ctx, "crypto", "derive", {"passphrase": password})

    def _crypto_seal(self, ctx, plaintext: str, password: str, salt: str = "") -> str:
        mgr = self._mgr_get(ctx.kernel)
        salt_b64 = salt
        if not salt_b64:
            d = mgr.call("crypto", "derive", {"passphrase": password}, timeout=20)
            salt_b64 = d.get("salt_b64", "") if isinstance(d, dict) else ""
        if not salt_b64:
            return "[错误] 无法获取 salt"
        res = mgr.call("crypto", "seal",
                       {"passphrase": password, "salt_b64": salt_b64, "plaintext": plaintext},
                       timeout=20)
        if isinstance(res, dict):
            res = {"salt_b64": salt_b64, **res}
        return _fmt(res)

    def _crypto_unseal(self, ctx, blob: str, salt: str, password: str) -> str:
        return self._call(ctx, "crypto", "unseal",
                          {"passphrase": password, "salt_b64": salt, "blob": blob})

    def _crypto_sign(self, ctx, message: str, password: str = "", salt: str = "",
                     key_b64: str = "") -> str:
        params = {"message": message}
        if key_b64:
            params["key_b64"] = key_b64
        else:
            params["passphrase"] = password
            params["salt_b64"] = salt
        return self._call(ctx, "crypto", "sign", params, timeout=20)

    def _crypto_verify(self, ctx, message: str, mac_b64: str, password: str = "",
                       salt: str = "", key_b64: str = "") -> str:
        params = {"message": message, "mac_b64": mac_b64}
        if key_b64:
            params["key_b64"] = key_b64
        else:
            params["passphrase"] = password
            params["salt_b64"] = salt
        return self._call(ctx, "crypto", "verify", params, timeout=20)

    def _index_build(self, ctx, root: str) -> str:
        return self._call(ctx, "index", "build", {"root": root}, timeout=120)

    def _index_query(self, ctx, symbol: str) -> str:
        return self._call(ctx, "index", "query", {"symbol": symbol})

    def _index_stats(self, ctx) -> str:
        return self._call(ctx, "index", "stats", {})

    def _ansi_strip(self, ctx, text: str) -> str:
        return self._call(ctx, "ansi", "strip", {"text": text})

    def _ansi_render(self, ctx, text: str, mode: str = "plain") -> str:
        return self._call(ctx, "ansi", "render", {"text": text, "format": mode})

    def _rules_check(self, ctx, path: str, content: str) -> str:
        return self._call(ctx, "rules", "check", {"path": path, "content": content})

    def _rules_load(self, ctx, rules_yaml: str = "", rules_path: str = "") -> str:
        params = {}
        if rules_yaml:
            params["rules_yaml"] = rules_yaml
        elif rules_path:
            params["rules_path"] = rules_path
        else:
            return "[错误] 需提供 rules_yaml 或 rules_path"
        return self._call(ctx, "rules", "load", params)

    def _rules_lint(self, ctx, files: str) -> str:
        # files: JSON 数组 [{path, content}]
        try:
            fl = json.loads(files) if files else []
        except json.JSONDecodeError:
            return "[错误] files 不是合法 JSON 数组"
        return self._call(ctx, "rules", "lint", {"files": fl})

    def _plugin_invoke(self, ctx, plugin: str, method: str, params: str = "{}") -> str:
        try:
            p = json.loads(params) if params else {}
        except json.JSONDecodeError:
            return "[错误] params 不是合法 JSON"
        return self._call(ctx, "plugin-host", "invoke",
                          {"plugin": plugin, "method": method, "params": p})

    def _skill_search(self, ctx, query: str) -> str:
        return self._call(ctx, "skill-market", "search", {"query": query})

    def _mcp_call(self, ctx, command: str, args: str = "[]", name: str = "", arguments: str = "{}") -> str:
        try:
            a = json.loads(args) if args else []
            kv = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return "[错误] args/arguments 不是合法 JSON"
        mgr = self._mgr_get(ctx.kernel)
        try:
            mgr.call("mcp-client", "connect", {"command": command, "args": a}, timeout=20)
            res = mgr.call("mcp-client", "call", {"name": name, "arguments": kv}, timeout=20)
            mgr.call("mcp-client", "disconnect", {}, timeout=10)
            return _fmt(res)
        except IpcError as exc:
            return f"[MCP 错误] {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"[MCP 异常] {type(exc).__name__}: {exc}"

    def _dashboard_snapshot(self, ctx, home: str = "", port: int = 18765) -> str:
        mgr = self._mgr_get(ctx.kernel)
        try:
            home = home or ctx.workspace
            mgr.call("dashboard", "start", {"home": home, "port": port}, timeout=15)
            snap = mgr.call("dashboard", "snapshot", {}, timeout=15)
            mgr.call("dashboard", "stop", {}, timeout=10)
            return _fmt(snap)
        except IpcError as exc:
            return f"[仪表盘错误] {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"[仪表盘异常] {type(exc).__name__}: {exc}"

    def _search(self, ctx, pattern: str, path: str = "", glob: str = "",
                 context: int = 2, max: int = 200, case_insensitive: bool = False) -> str:
        params = {"pattern": pattern, "context": context, "max": max,
                  "case_insensitive": case_insensitive}
        if path:
            params["path"] = path
        if glob:
            params["glob"] = glob
        return self._call(ctx, "search", "search", params, timeout=60)

    def _notify(self, ctx, title: str = "青小团", message: str = "", level: str = "info") -> str:
        if not message:
            return "[错误] 缺少 message"
        return self._call(ctx, "notify", "send", {"title": title, "message": message, "level": level})

    # -------------------------------------------------- 安全护栏 (最小影响半径)
    def _safety_score(self, ctx, command: str) -> str:
        """对单条命令做风险评分 (供 shell 执行前调用)。"""
        return self._call(ctx, "safety", "score", {"command": command}, timeout=15)

    def _safety_analyze(self, ctx, ops_json: str) -> str:
        """批量分析一组计划中的操作 (dry-run), 返回整体风险与逐条理由。
        ops_json 为 JSON 数组, 每项: {"kind":"command|write|sql|delete",
        "target":..., "text":...}"""
        try:
            ops = json.loads(ops_json) if ops_json else []
        except json.JSONDecodeError as exc:
            return f"[错误] ops 不是合法 JSON: {exc}"
        if not isinstance(ops, list):
            return "[错误] ops 必须是数组"
        return self._call(ctx, "safety", "analyze", {"ops": ops}, timeout=20)

    # -------------------------------------------------- 结构化 JSON 引擎
    def _json_pointer(self, ctx, doc: str, pointer: str) -> str:
        """按 RFC 6901 JSON Pointer 从文档取值。"""
        try:
            doc_obj = json.loads(doc) if doc else {}
        except json.JSONDecodeError as exc:
            return f"[错误] doc 不是合法 JSON: {exc}"
        if not pointer:
            return "[错误] 缺少 pointer"
        return self._call(ctx, "json", "pointer", {"doc": doc_obj, "pointer": pointer}, timeout=15)

    def _json_diff(self, ctx, a_json: str, b_json: str) -> str:
        """结构化比较两个 JSON 文档, 返回逐路径增/删/改。"""
        try:
            a = json.loads(a_json) if a_json else {}
            b = json.loads(b_json) if b_json else {}
        except json.JSONDecodeError as exc:
            return f"[错误] JSON 解析失败: {exc}"
        return self._call(ctx, "json", "diff", {"a": a, "b": b}, timeout=15)

    def _json_merge(self, ctx, base_json: str, overlay_json: str) -> str:
        """深合并两个 JSON 文档 (overlay 覆盖 base), 用于配置合并。"""
        try:
            base = json.loads(base_json) if base_json else {}
            overlay = json.loads(overlay_json) if overlay_json else {}
        except json.JSONDecodeError as exc:
            return f"[错误] JSON 解析失败: {exc}"
        return self._call(ctx, "json", "merge", {"base": base, "overlay": overlay}, timeout=15)

    def _agent_sdk(self, ctx, action: str = "config", message: str = "",
                   base_url: str = "", api_key: str = "", model: str = "",
                   system: str = "") -> str:
        mgr = self._mgr_get(ctx.kernel)
        try:
            if action == "config":
                mgr.call("agent-sdk", "config",
                         {"base_url": base_url, "api_key": api_key, "model": model, "system": system},
                         timeout=15)
                return _fmt({"ok": True, "configured": True})
            if action == "reset":
                return _fmt(mgr.call("agent-sdk", "reset", {}, timeout=15))
            if action == "run":
                return _fmt(mgr.call("agent-sdk", "run", {"message": message, "stream": False}, timeout=120))
            return "[错误] 未知 action"
        except IpcError as exc:
            return f"[Agent SDK 错误] {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"[Agent SDK 异常] {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 激活/注册

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("ext.enabled", True):
            # 即使 ext 工具不注册, 仍提供 safety_check 服务 (纯 C 引擎, 独立可用)
            pass
        registry = kernel.require("tool_registry")
        # 把 safety 拦截能力注册为内核服务, 供 shell.py 等复用同一 IPC 进程
        kernel.provide("safety_check", _SafetyService(self), owner=self.name)

        registry.register(Tool(
            name="ext_diff", group="external",
            description="用 C 引擎做 Myers 差分, 返回 unified diff 文本",
            parameters={"type": "object", "properties": {
                "old": string_prop("旧文本"), "new": string_prop("新文本")},
                "required": ["old", "new"]},
            handler=self._diff))

        registry.register(Tool(
            name="ext_patch", group="external",
            description="把 diff 补丁应用到原文上",
            parameters={"type": "object", "properties": {
                "patch": string_prop("diff 补丁文本"), "content": string_prop("被补丁的文本")},
                "required": ["patch", "content"]},
            handler=self._patch))

        registry.register(Tool(
            name="ext_merge3", group="external",
            description="三路合并 (base/a/b), 返回合并结果",
            parameters={"type": "object", "properties": {
                "base": string_prop("基准文本"), "a": string_prop("改动A"), "b": string_prop("改动B")},
                "required": ["base", "a", "b"]},
            handler=self._merge3))

        registry.register(Tool(
            name="ext_crypto_derive", group="external",
            description="生成随机 salt (供 seal 使用, PBKDF2 在引擎内部完成)",
            parameters={"type": "object", "properties": {
                "password": string_prop("口令 (用于派生 salt 关联)")},
                "required": ["password"]},
            handler=self._crypto_derive))

        registry.register(Tool(
            name="ext_crypto_seal", group="external",
            description="用口令做 AES-256-GCM 加密, 返回 salt_b64 与 blob(密文)",
            parameters={"type": "object", "properties": {
                "plaintext": string_prop("明文"), "password": string_prop("口令"),
                "salt": string_prop("盐 base64 (可选, 缺省自动生成)")},
                "required": ["plaintext", "password"]},
            handler=self._crypto_seal))

        registry.register(Tool(
            name="ext_crypto_unseal", group="external",
            description="AES-256-GCM 解密, 需 blob/salt/口令",
            parameters={"type": "object", "properties": {
                "blob": string_prop("密文 blob (base64)"), "salt": string_prop("盐 base64"),
                "password": string_prop("口令")},
                "required": ["blob", "salt", "password"]},
            handler=self._crypto_unseal))

        registry.register(Tool(
            name="ext_crypto_sign", group="external",
            description="HMAC-SHA256 签名 (内容认证/完整性), 返回 mac_b64",
            parameters={"type": "object", "properties": {
                "message": string_prop("待签名消息"),
                "password": string_prop("口令 (与 salt 配合派生密钥)"),
                "salt": string_prop("盐 base64 (配合口令)"),
                "key_b64": string_prop("直接给 32 字节密钥 base64 (跳过派生, 可选)")},
                "required": ["message"]},
            handler=self._crypto_sign))

        registry.register(Tool(
            name="ext_crypto_verify", group="external",
            description="校验 HMAC-SHA256 签名, 返回 ok:true/false",
            parameters={"type": "object", "properties": {
                "message": string_prop("待验签消息"),
                "mac_b64": string_prop("签名 base64"),
                "password": string_prop("口令 (与 salt 配合派生密钥)"),
                "salt": string_prop("盐 base64 (配合口令)"),
                "key_b64": string_prop("直接给 32 字节密钥 base64 (跳过派生, 可选)")},
                "required": ["message", "mac_b64"]},
            handler=self._crypto_verify))

        registry.register(Tool(
            name="ext_index_build", group="external",
            description="用 C 引擎增量索引一个目录 (符号/语言统计)",
            parameters={"type": "object", "properties": {
                "root": string_prop("要索引的根目录")}, "required": ["root"]},
            handler=self._index_build))

        registry.register(Tool(
            name="ext_index_query", group="external",
            description="查询已索引的符号定义位置",
            parameters={"type": "object", "properties": {
                "symbol": string_prop("符号名")}, "required": ["symbol"]},
            handler=self._index_query))

        registry.register(Tool(
            name="ext_index_stats", group="external",
            description="返回当前索引的统计信息",
            parameters={"type": "object", "properties": {}},
            handler=self._index_stats))

        registry.register(Tool(
            name="ext_ansi_strip", group="external",
            description="剥离 ANSI 转义序列, 得到纯文本",
            parameters={"type": "object", "properties": {
                "text": string_prop("含 ANSI 转义的文本")}, "required": ["text"]},
            handler=self._ansi_strip))

        registry.register(Tool(
            name="ext_ansi_render", group="external",
            description="把 ANSI 转义渲染为纯文本或 HTML",
            parameters={"type": "object", "properties": {
                "text": string_prop("含 ANSI 转义的文本"),
                "mode": string_prop("渲染模式: plain | html")},
                "required": ["text"]},
            handler=self._ansi_render))

        registry.register(Tool(
            name="ext_rules_load", group="external",
            description="向 TS 规则引擎载入 YAML 规则 (rules_yaml 或 rules_path 二选一)",
            parameters={"type": "object", "properties": {
                "rules_yaml": string_prop("规则 YAML 文本"),
                "rules_path": string_prop("规则文件路径")},
                "required": []},
            handler=self._rules_load))

        registry.register(Tool(
            name="ext_rules_check", group="external",
            description="用 TS 规则引擎对单个文件做策略校验 (需先 load 规则)",
            parameters={"type": "object", "properties": {
                "path": string_prop("文件路径"), "content": string_prop("文件内容")},
                "required": ["path", "content"]},
            handler=self._rules_check))

        registry.register(Tool(
            name="ext_rules_lint", group="external",
            description="对一批文件做规则校验, 返回报告 (files 为 JSON 数组 [{path,content}])",
            parameters={"type": "object", "properties": {
                "files": string_prop("JSON 数组 [{path, content}]")},
                "required": ["files"]},
            handler=self._rules_lint))

        registry.register(Tool(
            name="ext_plugin_invoke", group="external",
            description="调用一个已加载的外部插件方法",
            parameters={"type": "object", "properties": {
                "plugin": string_prop("插件名"), "method": string_prop("方法名"),
                "params": string_prop("方法参数 JSON (可选)")},
                "required": ["plugin", "method"]},
            handler=self._plugin_invoke))

        registry.register(Tool(
            name="ext_skill_search", group="external",
            description="在技能市场 registry 里搜索技能",
            parameters={"type": "object", "properties": {
                "query": string_prop("查询词")}, "required": ["query"]},
            handler=self._skill_search))

        registry.register(Tool(
            name="ext_mcp_call", group="external",
            description="连接一个 MCP server (stdio) 并调用其工具",
            parameters={"type": "object", "properties": {
                "command": string_prop("MCP server 启动命令 (如 node)"),
                "args": string_prop("启动参数 JSON 数组"),
                "name": string_prop("要调用的工具名"),
                "arguments": string_prop("工具参数 JSON 对象")},
                "required": ["command", "name"]},
            handler=self._mcp_call))

        registry.register(Tool(
            name="ext_dashboard_snapshot", group="external",
            description="抓取本地仪表盘的会话/记忆快照",
            parameters={"type": "object", "properties": {
                "home": string_prop("青小团 home 目录 (缺省工作区)"),
                "port": {"type": "integer", "description": "仪表盘端口"}},
                "required": []},
            handler=self._dashboard_snapshot))

        registry.register(Tool(
            name="ext_agent_sdk", group="external",
            description="驱动 TS 版 Agent SDK (action: config | run | reset)",
            parameters={"type": "object", "properties": {
                "action": string_prop("操作: config | run | reset"),
                "message": string_prop("run 时的用户输入"),
                "base_url": string_prop("OpenAI 兼容 endpoint"),
                "api_key": string_prop("API Key"),
                "model": string_prop("模型名"),
                "system": string_prop("系统提示 (可选)")},
                "required": []},
            handler=self._agent_sdk))

        registry.register(Tool(
            name="ext_search", group="external",
            description="本地全文检索 (ripgrep 风格): 在工作区递归搜索正则, 返回匹配行与上下文",
            parameters={"type": "object", "properties": {
                "pattern": string_prop("正则表达式"),
                "path": string_prop("限定子目录 (可选)"),
                "glob": string_prop("仅匹配某类文件名, 如 *.py (可选)"),
                "context": {"type": "integer", "description": "上下文行数 (默认 2)"},
                "max": {"type": "integer", "description": "最大匹配数 (默认 200)"},
                "case_insensitive": {"type": "boolean", "description": "忽略大小写 (默认 false)"}},
                "required": ["pattern"]},
            handler=self._search))

        registry.register(Tool(
            name="ext_notify", group="external",
            description="发送跨平台桌面通知 (Windows toast / macOS osascript / Linux notify-send)",
            parameters={"type": "object", "properties": {
                "title": string_prop("标题 (默认 青小团)"),
                "message": string_prop("通知内容"),
                "level": string_prop("级别: info | success | warning | error")},
                "required": ["message"]},
            handler=self._notify))

        registry.register(Tool(
            name="ext_safety_score", group="external",
            description="对单条命令做安全风险评估 (最小影响半径): 返回 risk(none/low/medium/high/critical)、blast_radius、命中原因与安全替代建议。critical 级会自动建议阻断。",
            parameters={"type": "object", "properties": {
                "command": string_prop("待评估的命令字符串")},
                "required": ["command"]},
            handler=self._safety_score))

        registry.register(Tool(
            name="ext_safety_analyze", group="external",
            description="批量分析一组计划中的操作 (dry-run, 不执行), 返回整体风险等级、行动建议 (BLOCK/CONFIRM/REVIEW/ok) 与逐条理由。operations 为 JSON 数组, 每项 {kind,target?,text?}。",
            parameters={"type": "object", "properties": {
                "operations": string_prop("JSON 数组, 每项形如 {\"kind\":\"command|write|sql|delete\", \"target\":\"文件路径\", \"text\":\"命令或SQL\"}")},
                "required": ["operations"]},
            handler=self._safety_analyze))

        registry.register(Tool(
            name="ext_json_pointer", group="external",
            description="按 RFC 6901 JSON Pointer 从 JSON 文档精确取值 (如 /a/b/0/c)。",
            parameters={"type": "object", "properties": {
                "doc": string_prop("JSON 文档字符串"),
                "pointer": string_prop("JSON Pointer 路径, 如 /users/0/name")},
                "required": ["doc", "pointer"]},
            handler=self._json_pointer))

        registry.register(Tool(
            name="ext_json_diff", group="external",
            description="结构化比较两个 JSON 文档, 返回逐路径的增/删/改 (用于配置/数据迁移评审)。",
            parameters={"type": "object", "properties": {
                "a": string_prop("原始 JSON"), "b": string_prop("新 JSON")},
                "required": ["a", "b"]},
            handler=self._json_diff))

        registry.register(Tool(
            name="ext_json_merge", group="external",
            description="深合并两个 JSON 文档 (overlay 覆盖 base), 用于分层配置合并。",
            parameters={"type": "object", "properties": {
                "base": string_prop("基础 JSON"), "overlay": string_prop("覆盖层 JSON")},
                "required": ["base", "overlay"]},
            handler=self._json_merge))

    def deactivate(self, kernel: Kernel) -> None:
        if self._mgr is not None:
            self._mgr.close_all()
            self._mgr = None

    # ------------------------------------------------------------ 内核服务实现
    def safety_check(self, ctx: ToolContext, command: str) -> dict:
        """结构化安全评分 (供内核服务调用): 返回 dict 而非字符串。
        失败时返回低风险占位, 不阻断正常命令执行。"""
        try:
            raw = self._call(ctx, "safety", "score", {"command": command}, timeout=15)
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {"risk": "none", "block": False, "unavailable": True,
                            "reasons": [raw], "blast_radius": 1, "safe_preview": []}
            return raw if isinstance(raw, dict) else {"risk": "none", "block": False}
        except Exception as exc:  # noqa: BLE001
            return {"risk": "none", "block": False, "unavailable": True,
                    "reasons": [f"安全引擎调用失败: {type(exc).__name__}: {exc}"],
                    "blast_radius": 1, "safe_preview": []}


class _SafetyService:
    """内核服务包装: shell.py 通过 kernel.require('safety_check') 调用。"""

    def __init__(self, plugin: ExternalToolsPlugin) -> None:
        self._plugin = plugin

    def check(self, ctx: ToolContext, command: str) -> dict:
        return self._plugin.safety_check(ctx, command)
