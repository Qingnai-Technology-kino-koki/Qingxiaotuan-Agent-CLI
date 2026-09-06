"""MCP HTTP 客户端 —— 通过 JSON-RPC over HTTP 与远端 MCP server 通信。

用 httpx 实现，不依赖官方 ``mcp`` SDK：
- 请求：POST ``application/json``，body 为 JSON-RPC 信封；``Accept`` 同时声明
  ``application/json, text/event-stream`` 以兼容 Streamable HTTP。
- 响应：JSON 直接解析；``text/event-stream`` 取 ``data:`` 行聚合为 JSON。
- 会话：捕获响应头 ``Mcp-Session-Id`` 并在后续请求回传。
- 鉴权：``bearerTokenEnvVar`` 注入 ``Authorization: Bearer <token>``。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable, Dict, List, Optional

import httpx

from .config import build_remote_headers
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


class HttpMcpClient:
    """经 Streamable HTTP 与 MCP server 通信的客户端（对应 ``HttpMcpClient``）。"""

    CLIENT_NAME = "qingxiaotuan"
    CLIENT_VERSION = "0.4"
    PROTOCOL_VERSION = "2024-11-05"

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
        self._url = str(_cfg(config, "url"))
        self.startup_timeout = (startup_timeout_ms or 30_000) / 1000.0
        self.tool_timeout = (tool_timeout_ms / 1000.0) if tool_timeout_ms else 30.0
        self._env_lookup = env_lookup or (lambda n: os.environ.get(n))
        self._headers = build_remote_headers(config, self._env_lookup) or {}
        self._client_name = client_name or self.CLIENT_NAME
        self._client_version = client_version or self.CLIENT_VERSION
        self._req_id = 0
        self._session_id: Optional[str] = None
        self._closed = False
        self._ready = False
        self._last_error: Optional[BaseException] = None
        self._unexpected_close_listener: Optional[Callable[[UnexpectedCloseReason], None]] = None
        self._client = httpx.AsyncClient(timeout=self.tool_timeout)

    # ------------------------------------------------------------- 生命周期

    async def connect(self) -> None:
        if self._closed:
            raise McpError("MCP HTTP client is closed")
        if self._ready:
            return
        try:
            await asyncio_wait_for_optional(
                self._rpc(
                    "initialize",
                    {
                        "protocolVersion": self.PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": self._client_name, "version": self._client_version},
                    },
                ),
                self.startup_timeout,
            )
        except McpTimeoutError:
            raise
        except Exception as exc:
            raise McpError(f"MCP HTTP 握手失败: {exc}") from exc
        await self._rpc_notify("notifications/initialized", {})
        self._ready = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ready:
            try:
                await self._rpc("shutdown", {})
            except Exception:
                pass
            try:
                await self._rpc_notify("exit", {})
            except Exception:
                pass
        try:
            await self._client.aclose()
        except Exception:
            pass

    # ------------------------------------------------------------- MCP 接口

    async def list_tools(self) -> List[MCPToolDefinition]:
        result = await self._rpc("tools/list", {})
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
            result = await self._rpc("tools/call", params)
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
            await self._rpc("ping", {})
        except asyncio.TimeoutError:
            raise McpTimeoutError("MCP ping 超时")

    def on_unexpected_close(self, listener: Callable[[UnexpectedCloseReason], None]) -> None:
        self._unexpected_close_listener = listener

    # ------------------------------------------------------------- 传输

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        self._req_id += 1
        rid = self._req_id
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        return await self._post(body)

    async def _rpc_notify(self, method: str, params: Dict[str, Any]) -> None:
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            await self._post(body, expect_response=False)
        except Exception:
            pass

    async def _post(self, body: Dict[str, Any], expect_response: bool = True) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        headers.update(self._headers)
        try:
            resp = await self._client.post(
                self._url, content=json.dumps(body, ensure_ascii=False), headers=headers
            )
        except httpx.HTTPError as exc:
            self._last_error = exc
            raise McpConnectionError(f"MCP HTTP 请求失败: {exc}") from exc
        if "Mcp-Session-Id" in resp.headers:
            self._session_id = resp.headers["Mcp-Session-Id"]
        if expect_response:
            return self._parse_response(resp)
        return None

    @staticmethod
    def _parse_response(resp: httpx.Response) -> Any:
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        text = resp.text
        if "text/event-stream" in ct or text.lstrip().startswith("event:") or "data:" in text:
            data_parts: List[str] = []
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data_parts.append(line[len("data:") :].strip())
            combined = "\n".join(data_parts)
            if not combined:
                return None
            try:
                return json.loads(combined)
            except json.JSONDecodeError:
                return None
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise McpError(f"MCP HTTP 响应不是合法 JSON: {exc}") from exc


def asyncio_wait_for_optional(coro: Any, timeout: Optional[float]) -> Any:
    import asyncio

    if timeout is None:
        return coro
    return asyncio.wait_for(coro, timeout)
