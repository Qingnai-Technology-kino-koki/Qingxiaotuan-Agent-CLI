"""MCP stdio 客户端 —— 通过标准输入/输出与本地 MCP server 通信。

不引入官方 ``mcp`` SDK：用 ``asyncio.subprocess`` 拉起子进程，stdin/stdout 走
**JSON-RPC 2.0 行帧**（每行一个 JSON 对象，``\\n`` 结尾）。实现 initialize /
tools/list / tools/call / ping / shutdown，合并环境、startup 超时、stderr ring buffer、
意外断线检测。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable, Dict, List, Optional

from .config import McpServerStdioConfig
from .errors import McpConnectionError, McpError, McpTimeoutError
from .types import (
    MCPClient,
    MCPContentBlock,
    MCPToolDefinition,
    MCPToolResult,
    UnexpectedCloseReason,
)


def _cfg(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class StdioMcpClient:
    """经子进程 stdio 与 MCP server 通信的客户端（对应 ``StdioMcpClient``）。"""

    CLIENT_NAME = "qingxiaotuan"
    CLIENT_VERSION = "0.4"
    PROTOCOL_VERSION = "2024-11-05"
    STDERR_CAPACITY = 4 * 1024

    def __init__(
        self,
        config: Any,
        *,
        startup_timeout_ms: int = 30_000,
        tool_timeout_ms: Optional[int] = None,
        env_lookup: Optional[Callable[[str], str]] = None,
        client_name: Optional[str] = None,
        client_version: Optional[str] = None,
    ) -> None:
        self._command = _cfg(config, "command")
        self._args: List[str] = list(_cfg(config, "args") or [])
        self._env_extra: Dict[str, str] = dict(_cfg(config, "env") or {})
        self._cwd: Optional[str] = _cfg(config, "cwd")
        self.startup_timeout = (startup_timeout_ms or 30_000) / 1000.0
        self.tool_timeout = (tool_timeout_ms / 1000.0) if tool_timeout_ms else None
        self._env_lookup = env_lookup or (lambda n: os.environ.get(n))
        self._client_name = client_name or self.CLIENT_NAME
        self._client_version = client_version or self.CLIENT_VERSION

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._req_id = 0
        self._pending: Dict[int, "asyncio.Future"] = {}
        self._closed = False
        self._ready = False
        self._last_error: Optional[BaseException] = None
        self._stderr_chunks: List[str] = []
        self._stderr_len = 0
        self._unexpected_close_listener: Optional[Callable[[UnexpectedCloseReason], None]] = None

    # ------------------------------------------------------------- 生命周期

    async def connect(self) -> None:
        if self._closed:
            raise McpError("MCP stdio client is closed")
        if self._ready:
            return
        env = self._merge_env()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self._cwd,
            )
        except (OSError, ValueError) as exc:  # 进程拉起失败
            raise McpError(f"无法启动 MCP server 子进程 ({self._command}): {exc}") from exc

        self._reader_task = asyncio.ensure_future(self._read_loop())
        self._stderr_task = asyncio.ensure_future(self._stderr_loop())

        try:
            await asyncio.wait_for(
                self._request(
                    "initialize",
                    {
                        "protocolVersion": self.PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": self._client_name, "version": self._client_version},
                    },
                ),
                timeout=self.startup_timeout,
            )
            await self._notify("notifications/initialized", {})
        except asyncio.TimeoutError:
            await self._close_proc()
            raise McpTimeoutError(f"MCP 启动超时（>{self.startup_timeout:.0f}s）")
        except Exception:
            await self._close_proc()
            raise
        self._ready = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ready:
            try:
                await asyncio.wait_for(self._request("shutdown", {}), timeout=5.0)
            except Exception:
                pass
            try:
                await self._notify("exit", {})
            except Exception:
                pass
        await self._close_proc()

    async def _close_proc(self) -> None:
        proc = self._proc
        self._proc = None
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        self._reader_task = None
        self._stderr_task = None
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ------------------------------------------------------------- MCP 接口

    async def list_tools(self) -> List[MCPToolDefinition]:
        result = await self._request("tools/list", {})
        raw = (result or {}).get("tools", []) if isinstance(result, dict) else []
        out: List[MCPToolDefinition] = []
        for t in raw:
            if not isinstance(t, dict):
                continue
            out.append(
                MCPToolDefinition(
                    name=t.get("name", ""),
                    description=t.get("description", "") or "",
                    inputSchema=t.get("inputSchema", {}) or {},
                )
            )
        return out

    async def call_tool(
        self,
        name: str,
        args: Dict[str, Any],
        signal: Any = None,
    ) -> MCPToolResult:
        params = {"name": name, "arguments": args or {}}
        try:
            result = await self._call_with_timeout(
                self._request("tools/call", params), self.tool_timeout, signal
            )
        except asyncio.TimeoutError:
            raise McpTimeoutError(f"MCP call_tool 超时: {name}")
        if not isinstance(result, dict):
            result = {}
        content = result.get("content", []) or []
        blocks = [MCPContentBlock.from_dict(b) for b in content]
        return MCPToolResult(
            content=blocks,
            isError=bool(result.get("isError", False)),
            structuredContent=result.get("structuredContent"),
            meta=result.get("_meta") if isinstance(result.get("_meta"), dict) else None,
        )

    async def ping(self) -> None:
        try:
            await asyncio.wait_for(self._request("ping", {}), timeout=5.0)
        except asyncio.TimeoutError:
            raise McpTimeoutError("MCP ping 超时")

    def on_unexpected_close(self, listener: Callable[[UnexpectedCloseReason], None]) -> None:
        self._unexpected_close_listener = listener

    # ------------------------------------------------------------- JSON-RPC 帧

    async def _request(self, method: str, params: Dict[str, Any]) -> Any:
        if self._proc is None or self._proc.stdin is None:
            raise McpConnectionError("MCP server 进程未运行")
        self._req_id += 1
        rid = self._req_id
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future" = loop.create_future()
        self._pending[rid] = fut
        try:
            await self._send(method, params, rid)
            return await fut
        finally:
            self._pending.pop(rid, None)

    async def _notify(self, method: str, params: Dict[str, Any]) -> None:
        await self._send(method, params, None)

    async def _send(self, method: str, params: Dict[str, Any], rid: Optional[int]) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if rid is not None:
            msg["id"] = rid
        msg["params"] = params
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpConnectionError("MCP server 进程未运行")
        try:
            proc.stdin.write(data)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionError) as exc:
            raise McpConnectionError("写入 MCP server stdin 失败") from exc

    async def _respond_error(self, rid: int, code: int, message: str) -> None:
        msg = {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(data)
            await proc.stdin.drain()
        except Exception:
            pass

    async def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = msg.get("id")
                if isinstance(rid, (int, str)):
                    fut = self._pending.get(rid)  # type: ignore[arg-type]
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(self._rpc_error(msg["error"]))
                        else:
                            fut.set_result(msg.get("result"))
                    else:
                        # server 主动发起的请求（非本地pending）：返回 method not found
                        await self._respond_error(int(rid) if isinstance(rid, int) else rid, -32601, "Method not found")
                # 通知（无 id）忽略
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # 读取出错 -> 记录并视作断线
            self._last_error = exc
        finally:
            # 进程退出：唤醒所有挂起请求并触发断线回调
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(McpConnectionError("MCP server 连接已关闭"))
            self._pending.clear()
            if not self._closed:
                self._fire_unexpected_close()

    async def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                self._push_stderr(raw.decode("utf-8", errors="replace"))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    # ------------------------------------------------------------- 工具方法

    def _merge_env(self) -> Dict[str, str]:
        """合并父进程环境 + 配置 env（对应 mergeStdioEnv，简化为直接合并）。"""
        merged = {k: v for k, v in os.environ.items() if v is not None}
        merged.update(self._env_extra)
        return merged

    def _push_stderr(self, chunk: str) -> None:
        self._stderr_chunks.append(chunk)
        self._stderr_len += len(chunk)
        while self._stderr_len > self.STDERR_CAPACITY and self._stderr_chunks:
            removed = self._stderr_chunks.pop(0)
            self._stderr_len -= len(removed)

    def stderr_snapshot(self) -> str:
        return "".join(self._stderr_chunks)

    def _fire_unexpected_close(self) -> None:
        listener = self._unexpected_close_listener
        if listener is None:
            return
        reason: UnexpectedCloseReason = {
            "error": self._last_error,
            "stderr": self.stderr_snapshot() or None,
        }
        try:
            listener(reason)
        except Exception:
            pass

    @staticmethod
    def _rpc_error(err: Any) -> McpError:
        if isinstance(err, dict):
            return McpError(err.get("message", str(err)))
        return McpError(str(err))

    async def _call_with_timeout(self, coro: Any, timeout: Optional[float], signal: Any) -> Any:
        if signal is not None and hasattr(signal, "is_set"):
            done, pending = await asyncio.wait(
                {asyncio.ensure_future(coro), asyncio.ensure_future(signal.wait())},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
            if signal.is_set():
                raise McpConnectionError("被信号中止")
            for d in done:
                return d.result()
        if timeout is not None:
            return await asyncio.wait_for(coro, timeout)
        return await coro
