"""MCP (Model Context Protocol) 客户端 —— 通过 stdio 子进程与 MCP Server 通信。

传输: stdio。协议: JSON-RPC 2.0 (带 id 的请求/响应 + 可选的 notify)。
握手流程: initialize -> notifications/initialized -> tools/list -> tools/call。

安全增强:
- 工具调用权限检查 (白名单/黑名单)
- 调用审计日志
- 超时与重试策略
- 调用频率限制
- 敏感参数过滤
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional, Set

from .audit import MCPAuditStore


# 敏感参数关键词 (匹配则记录审计但不阻断)
_SENSITIVE_PARAM_PATTERNS = (
    re.compile(r"(password|secret|token|key|credential)", re.IGNORECASE),
)

# 危险工具关键词 (需要额外确认)
_DANGEROUS_TOOL_PATTERNS = (
    re.compile(r"(delete|remove|destroy|drop|execute|run|write)", re.IGNORECASE),
)


class MCPError(Exception):
    pass


class MCPSecurityPolicy:
    """MCP 安全策略。"""

    def __init__(
        self,
        allowed_tools: Optional[Set[str]] = None,
        denied_tools: Optional[Set[str]] = None,
        require_confirm_tools: Optional[Set[str]] = None,
        max_calls_per_minute: int = 60,
        audit_enabled: bool = True,
        sandbox_enabled: bool = False,
    ) -> None:
        self.allowed_tools = allowed_tools  # None = 不限制
        self.denied_tools = denied_tools or set()
        self.require_confirm_tools = require_confirm_tools or set()
        self.max_calls_per_minute = max_calls_per_minute
        self.audit_enabled = audit_enabled
        # 沙箱隔离: 启用后 MCP server 子进程以脱敏环境 + 临时隔离目录运行,
        # 且每次工具调用前经安全闸门 fail-closed 判定 (见 _sandbox_gate)。
        self.sandbox_enabled = sandbox_enabled
        self._call_times: List[float] = []
        self._lock = threading.Lock()
    
    def check_tool_allowed(self, tool_name: str) -> tuple[bool, str]:
        """检查工具是否允许调用。返回 (allowed, reason)。"""
        # 黑名单优先
        if tool_name in self.denied_tools:
            return False, f"工具 '{tool_name}' 在黑名单中"

        # 白名单检查 (None = 不限制; "*" = 允许所有工具, 与 MCP_SECURITY.md 文档一致)
        if self.allowed_tools is not None and "*" not in self.allowed_tools \
                and tool_name not in self.allowed_tools:
            return False, f"工具 '{tool_name}' 不在白名单中"

        return True, ""
    
    def requires_confirm(self, tool_name: str) -> bool:
        """检查工具是否需要额外确认。"""
        if tool_name in self.require_confirm_tools:
            return True
        # 自动检测危险工具
        for pattern in _DANGEROUS_TOOL_PATTERNS:
            if pattern.search(tool_name):
                return True
        return False
    
    def check_rate_limit(self) -> tuple[bool, str]:
        """检查调用频率限制。"""
        with self._lock:
            now = time.time()
            # 清理一分钟前的记录
            self._call_times = [t for t in self._call_times if now - t < 60]
            
            if len(self._call_times) >= self.max_calls_per_minute:
                return False, f"超过频率限制 ({self.max_calls_per_minute}/分钟)"
            
            self._call_times.append(now)
            return True, ""
    
    def audit_call(self, tool_name: str, arguments: Dict[str, Any], result: str, success: bool) -> Dict[str, Any]:
        """记录审计日志。"""
        if not self.audit_enabled:
            return {}
        
        # 检查敏感参数
        sensitive_params = []
        for key, value in arguments.items():
            for pattern in _SENSITIVE_PARAM_PATTERNS:
                if pattern.search(key) or (isinstance(value, str) and pattern.search(value)):
                    sensitive_params.append(key)
                    break
        
        return {
            "timestamp": time.time(),
            "tool": tool_name,
            "arguments": {k: "***" if k in sensitive_params else v for k, v in arguments.items()},
            "success": success,
            "result_length": len(result),
            "sensitive_params": sensitive_params,
        }


class MCPClient:
    """与一个 MCP Server 的 stdio 连接 (同步接口, 内部异步 I/O)。"""

    def __init__(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        security_policy: Optional[MCPSecurityPolicy] = None,
        max_retries: int = 2,
        retry_delay: float = 1.0,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._proc: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._read_task: Any = None
        self._req_id = 0
        self._pending: Dict[Any, "asyncio.Future"] = {}
        self._tools: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._initialized = False
        # 安全策略
        self.security_policy = security_policy or MCPSecurityPolicy()
        self._audit_log: List[Dict[str, Any]] = []
        # 审计持久化存储 (可注入; 默认全局单例落到 ~/.qingxiaotuan/mcp-audit.jsonl)
        self._audit_store: Optional[MCPAuditStore] = None
        self._sandbox_dir: Optional[str] = None

    # ---------------------------------------------------------- 审计/沙箱辅助

    def set_audit_store(self, store: MCPAuditStore) -> None:
        """注入审计存储 (默认为 ~/.qingxiaotuan/mcp-audit.jsonl 单例)。"""
        self._audit_store = store

    def _persist_audit(self, entry: Dict[str, Any]) -> None:
        """把审计记录落到磁盘 (默认存储 + 注入存储)。"""
        try:
            self._audit_log.append(entry)
        except Exception:  # noqa: BLE001
            pass
        if entry and (self._audit_store is not None or self.security_policy.audit_enabled):
            store = self._audit_store or _get_global_audit_store()
            record = dict(entry)
            record.setdefault("server", self.name)
            record.setdefault("tool", entry.get("tool", ""))
            store.record(record)

    def _sandbox_gate(self, tool_name: str, arguments: Dict[str, Any]) -> str | None:
        """沙箱模式下的调用前安全闸门 (fail-closed): 命中危险操作直接拒绝。

        复用统一安全闸门 SecurityGate.decide_mcp_tool —— 它内部对 MCP 工具名 + 参数
        里的文本字段做 safety_engine 红线检测。返回拦截原因字符串, None 表示放行。
        """
        try:
            from ...ext.security_gate import SecurityGate

            verdict = SecurityGate().decide_mcp_tool(tool_name, arguments)
            if verdict.blocks():
                reason = verdict.reasons[0] if verdict.reasons else "参数含危险操作"
                entry = self.security_policy.audit_call(
                    tool_name, arguments, reason, False)
                entry["note"] = "sandbox-gate-denied"
                self._persist_audit(entry)
                return f"[MCP 沙箱拦截] {reason}"
        except Exception:  # noqa: BLE001 - 沙箱闸门异常时保守拒绝
            return f"[MCP 沙箱拦截] 安全闸门异常, 保守拒绝调用: {tool_name}"
        return None

    def _sandbox_boot_env(self) -> Dict[str, str]:
        """沙箱模式: 脱敏后的子进程环境 (抹除密钥类变量), 防止密钥泄漏到 MCP server。"""
        from .sandbox import _sanitize_env

        return _sanitize_env(os.environ, self.env)

    def _sandbox_cwd(self) -> str | None:
        """沙箱模式: 为 MCP server 子进程准备隔离工作目录。

        返回一个空的临时目录 (server 无法读写工作区文件)。非沙箱模式返回 None
        (沿用继承的进程 cwd)。
        """
        if not self.security_policy.sandbox_enabled:
            return None
        import tempfile

        self._sandbox_dir = tempfile.mkdtemp(prefix="mcp_sandbox_")
        return self._sandbox_dir

    # ---------------------------------------------------------- 生命周期

    def start(self) -> None:
        """启动子进程并完成握手。幂等: 已启动则直接返回。"""
        if self._proc is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        boot = asyncio.run_coroutine_threadsafe(self._boot(), self._loop)
        try:
            boot.result(self.timeout)
        except Exception:
            # 启动失败: 关停事件循环线程, 清理状态, 向上抛 (由 plugin 捕获记录)。
            try:
                if self._proc is not None:
                    self._proc.terminate()
            except Exception:
                pass
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread = None
            self._loop = None
            self._proc = None
            self._initialized = False
            raise

    async def _boot(self) -> None:
        sandbox_env = self._sandbox_boot_env() if self.security_policy.sandbox_enabled else None
        full_env = sandbox_env or {**os.environ, **self.env}
        sandbox_cwd = self._sandbox_cwd()
        self._proc = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=full_env,
            cwd=sandbox_cwd,
        )
        # 先启动读取任务 (并发读响应), 再发起握手 —— 避免读写互相阻塞。
        assert self._loop is not None
        self._read_task = self._loop.create_task(self._read_loop())
        try:
            await self._send("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qingxiaotuan", "version": "0.4"},
            })
            await self._send_notify("notifications/initialized", {})
            result = await self._request("tools/list", {})
        except Exception:
            # 握手失败也要回收子进程, 否则成为孤儿进程。
            try:
                self._proc.terminate()
            except Exception:
                pass
            raise
        self._tools = (result or {}).get("tools", [])
        self._initialized = True

    def stop(self) -> None:
        """优雅关闭: shutdown -> exit -> 终止子进程 -> 停止事件循环线程。"""
        if self._proc is None:
            return
        if self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop).result(self.timeout)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._proc = None
        self._initialized = False
        # 清理沙箱隔离目录
        if self._sandbox_dir:
            import shutil

            shutil.rmtree(self._sandbox_dir, ignore_errors=True)
            self._sandbox_dir = None

    async def _shutdown_async(self) -> None:
        if self._proc is None:
            return
        try:
            await asyncio.wait_for(self._request("shutdown", {}), timeout=5.0)
        except Exception:
            pass
        try:
            await self._send_notify("exit", {})
        except Exception:
            pass
        if self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
        try:
            self._proc.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except Exception:
            pass

    # ---------------------------------------------------------- 工具发现

    def list_tools(self) -> List[Dict[str, Any]]:
        if self._proc is None:
            self.start()
        return self._tools

    # ---------------------------------------------------------- 调用

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """调用 MCP 工具 (带安全检查和重试)。"""
        if self._proc is None:
            self.start()

        # 安全检查: 沙箱模式下的 fail-closed 闸门 (危险参数直接拒绝)
        if self.security_policy.sandbox_enabled:
            blocked = self._sandbox_gate(tool_name, arguments)
            if blocked is not None:
                return blocked

        # 安全检查: 工具权限
        allowed, reason = self.security_policy.check_tool_allowed(tool_name)
        if not allowed:
            self._persist_audit(self.security_policy.audit_call(
                tool_name, arguments, reason, False
            ))
            return f"[MCP 安全拦截] {reason}"

        # 安全检查: 频率限制
        rate_ok, rate_reason = self.security_policy.check_rate_limit()
        if not rate_ok:
            self._persist_audit(self.security_policy.audit_call(
                tool_name, arguments, rate_reason, False
            ))
            return f"[MCP 频率限制] {rate_reason}"

        # 带重试的调用
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                result = self._rpc("tools/call", {"name": tool_name, "arguments": arguments})
                # 成功: 记录审计
                text = self._format_result(result)
                self._persist_audit(self.security_policy.audit_call(
                    tool_name, arguments, text, True
                ))
                return text
            except MCPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (2 ** attempt))  # 指数退避
                    continue

        # 所有重试失败
        error_msg = f"[MCP 错误] {tool_name}: {last_error}"
        self._persist_audit(self.security_policy.audit_call(
            tool_name, arguments, error_msg, False
        ))
        return error_msg
    
    def _format_result(self, result: Any) -> str:
        """格式化 MCP 工具返回结果。"""
        if isinstance(result, dict):
            is_error = result.get("isError", False)
            content = result.get("content", [])
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(str(item))
            text = "\n".join(parts)
            if is_error:
                return f"[MCP 工具错误] {text}"
            return text
        return str(result)
    
    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取审计日志。"""
        return self._audit_log[-limit:]
    
    def clear_audit_log(self) -> None:
        """清空审计日志。"""
        self._audit_log.clear()

    # ---------------------------------------------------------- JSON-RPC 内部

    def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        if self._loop is None:
            raise MCPError("MCP 客户端未启动")
        fut: "Future[Any]" = asyncio.run_coroutine_threadsafe(
            self._request(method, params), self._loop
        )
        try:
            return fut.result(self.timeout)
        except asyncio.TimeoutError:
            raise MCPError(f"MCP 调用超时: {method}")

    async def _request(self, method: str, params: Dict[str, Any]):
        self._req_id += 1
        rid = self._req_id
        fut = self._loop.create_future()  # type: ignore[union-attr]
        self._pending[rid] = fut
        try:
            await self._send(method, params, rid)
            try:
                result = await asyncio.wait_for(asyncio.shield(fut), timeout=self.timeout)
            except asyncio.TimeoutError:
                self._pending.pop(rid, None)
                raise MCPError(f"MCP 调用超时: {method}")
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            self._pending.pop(rid, None)

    async def _send(self, method: str, params: Dict[str, Any], rid: Optional[int] = None) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if rid is not None:
            msg["id"] = rid
        msg["params"] = params
        line = json.dumps(msg, ensure_ascii=False)
        assert self._proc is not None and self._proc.stdin is not None
        # 子进程流是字节流 (即使 Windows ProactorEventLoop 亦然), 必须编码。
        self._proc.stdin.write(line.encode("utf-8") + b"\n")
        await self._proc.stdin.drain()

    async def _send_notify(self, method: str, params: Dict[str, Any]) -> None:
        await self._send(method, params)  # 通知不带 id

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                break  # 子进程退出
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = msg.get("id")
            if rid is not None and rid in self._pending:
                fut = self._pending[rid]
                if not fut.done():
                    if "error" in msg:
                        fut.set_exception(MCPError(msg["error"].get("message", str(msg["error"]))))
                    else:
                        fut.set_result(msg.get("result"))
            # 忽略 server 主动发来的通知 (如 logging)


# 全局审计存储单例 (进程内共享, 落盘 ~/.qingxiaotuan/mcp-audit.jsonl)
_global_audit_store: Optional[MCPAuditStore] = None
_global_audit_lock = threading.Lock()


def _get_global_audit_store() -> MCPAuditStore:
    global _global_audit_store
    with _global_audit_lock:
        if _global_audit_store is None:
            _global_audit_store = MCPAuditStore()
        return _global_audit_store
