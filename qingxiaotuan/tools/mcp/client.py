"""MCP (Model Context Protocol) 客户端 —— 通过 stdio 子进程与 MCP Server 通信。

传输: stdio。协议: JSON-RPC 2.0 (带 id 的请求/响应 + 可选的 notify)。
握手流程: initialize -> notifications/initialized -> tools/list -> tools/call。

设计:
- 每个 MCPClient 管理一个子进程。早期实现用「同步 for raw in proc.stdout」在 async
  函数里阻塞读, 会卡死事件循环 (initialize 永远发不出去) —— 这是死锁 bug。
- 现改为真正的异步 I/O: asyncio.create_subprocess_exec + 异步 readline/write,
  并在 boot 阶段先启动 read_loop 任务再发起握手, 读写并发互不阻塞。
- 对外仍是同步接口 (CLI 是同步环境): 用独立事件循环线程 + run_coroutine_threadsafe
  包裹单次调用, 长连接则复用该线程上的事件循环。

仅依赖标准库 (asyncio / subprocess / json / threading), 不引入额外重型依赖。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import Future
from typing import Any, Dict, List, Optional


class MCPError(Exception):
    pass


class MCPClient:
    """与一个 MCP Server 的 stdio 连接 (同步接口, 内部异步 I/O)。"""

    def __init__(self, name: str, command: str, args: Optional[List[str]] = None,
                 env: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.timeout = timeout
        self._proc: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._read_task: Any = None
        self._req_id = 0
        self._pending: Dict[Any, "asyncio.Future"] = {}
        self._tools: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._initialized = False

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
        full_env = {**os.environ, **self.env}
        self._proc = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=full_env,
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
