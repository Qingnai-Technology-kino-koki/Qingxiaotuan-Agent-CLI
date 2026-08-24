"""MCP (Model Context Protocol) 客户端 —— 通过 stdio 子进程与 MCP Server 通信。

传输: stdio。协议: JSON-RPC 2.0 (带 id 的请求/响应 + 可选的 notify)。
握手流程: initialize -> (可选) notifications/initialized -> tools/list -> tools/call。

设计:
- 每个 MCPClient 管理一个子进程, 内部用 asyncio 异步读写, 对外暴露同步方法
  (CLI 是同步环境, 用 asyncio.run 包裹单次调用, 长连接则复用事件循环线程)。
- tools/list 的结果被桥接为本地 Tool (见 plugin.py), tools/call 把本地调用转发到远端。

仅依赖标准库 (asyncio / subprocess / json / threading), 不引入额外重型依赖。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import uuid
from typing import Any, Dict, List, Optional


class MCPError(Exception):
    pass


class MCPClient:
    """与一个 MCP Server 的 stdio 连接 (同步接口)。"""

    def __init__(self, name: str, command: str, args: Optional[List[str]] = None,
                 env: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._req_id = 0
        self._pending: Dict[Any, "asyncio.Future"] = {}
        self._tools: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._initialized = False

    # ---------------------------------------------------------- 生命周期

    def start(self) -> None:
        if self._proc is not None:
            return
        full_env = {**os.environ, **self.env}
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            bufsize=1,
            text=True,
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        # 启动读取协程
        asyncio.run_coroutine_threadsafe(self._read_loop(), self._loop)
        # 初始化握手
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "qingxiaotuan", "version": "0.4"},
        })
        self._notify("notifications/initialized", {})
        self._tools = (self._rpc("tools/list", {}) or {}).get("tools", [])

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._rpc("shutdown", {})
        except Exception:
            pass
        try:
            self._notify("exit", {})
        except Exception:
            pass
        if self._proc.stdin:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None
        self._initialized = False

    # ---------------------------------------------------------- 工具发现

    def list_tools(self) -> List[Dict[str, Any]]:
        if not self._initialized and self._proc is None:
            self.start()
        return self._tools

    # ---------------------------------------------------------- 调用

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if self._proc is None:
            self.start()
        try:
            result = self._rpc("tools/call", {"name": tool_name, "arguments": arguments})
        except MCPError as exc:
            return f"[MCP 错误] {tool_name}: {exc}"
        # MCP tools/call 返回 { content: [...], isError?: bool }
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
                return f"[MCP 工具错误] {tool_name}: {text}"
            return text
        return str(result)

    # ---------------------------------------------------------- JSON-RPC 内部

    def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        if self._loop is None:
            raise MCPError("MCP 客户端未启动")
        fut: "asyncio.Future" = asyncio.run_coroutine_threadsafe(
            self._request(method, params), self._loop
        ).result(self.timeout)
        if fut.exception():
            raise fut.exception()  # type: ignore[raise]
        return fut.result()

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._send(method, params, notify=True), self._loop)

    async def _request(self, method: str, params: Dict[str, Any]):
        self._req_id += 1
        rid = self._req_id
        fut = self._loop.create_future()  # type: ignore[union-attr]
        self._pending[rid] = fut
        await self._send(method, params, rid)
        return await fut

    async def _send(self, method: str, params: Dict[str, Any], rid: Optional[int] = None, notify: bool = False) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            msg["params"] = params
        if rid is not None:
            msg["id"] = rid
        line = json.dumps(msg, ensure_ascii=False)
        if self._proc and self._proc.stdin:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()

    async def _read_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rid = msg.get("id")
            if rid is not None and rid in self._pending:
                fut = self._pending.pop(rid)
                if not fut.done():
                    if "error" in msg:
                        fut.set_exception(MCPError(msg["error"].get("message", str(msg["error"]))))
                    else:
                        fut.set_result(msg.get("result"))
            # 忽略 server 主动发来的通知 (如 logging)
        self._initialized = True
