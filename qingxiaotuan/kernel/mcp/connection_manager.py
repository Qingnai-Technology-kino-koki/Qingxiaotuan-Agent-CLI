"""MCP 连接管理器 —— 按 transport 管理单个/多个 MCP server 的连接生命周期。

- ``connect_all(configs)``：并发创建客户端 + 握手 + 发现工具（connectAndDiscoverTools）。
- ``connect`` / ``remove`` / ``reconnect`` / ``shutdown``。
- 状态变化经 ``on_status_change`` 广播：pending | connected | failed | disabled | needs-auth | removed。
- 按 transport 分发 client 实现（stdio / http / sse）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Dict, List, Optional

from .client_http import HttpMcpClient
from .client_sse import SseMcpClient
from .client_stdio import StdioMcpClient
from .config import (
    DEFAULT_STARTUP_TIMEOUT_MS,
    compute_enabled_names,
    is_remote_config,
)
from .errors import McpConnectionError, McpError, McpStartupError
from .types import McpServerConfigLike, MCPClient, MCPToolDefinition


class McpConnectionManager:
    """管理多个 MCP server 的连接生命周期与状态。"""

    def __init__(self, options: Optional[Dict[str, Any]] = None) -> None:
        self._options = options or {}
        self._entries: Dict[str, "_InternalEntry"] = {}
        self._listeners: set = set()
        self._default_startup_timeout = (
            self._options.get("startup_timeout_ms") or DEFAULT_STARTUP_TIMEOUT_MS
        )
        self._default_tool_timeout = self._options.get("tool_timeout_ms")
        self._env_lookup = self._options.get("env_lookup") or (lambda n: os.environ.get(n))

    # ----------------------------------------------------------- 订阅

    def on_status_change(self, listener: Callable[["McpServerEntry"], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def list(self) -> List["McpServerEntry"]:
        return [_public(e) for e in self._entries.values()]

    def get(self, name: str) -> Optional["McpServerEntry"]:
        e = self._entries.get(name)
        return _public(e) if e is not None else None

    def resolved(self, name: str) -> Optional[Dict[str, Any]]:
        e = self._entries.get(name)
        if e is None or e.status != "connected" or e.client is None or e.raw_tools is None:
            return None
        return {
            "client": e.client,
            "tools": e.raw_tools,
            "raw_tools": e.raw_tools,
            "enabled_names": e.enabled_names or set(),
        }

    def config_of(self, name: str) -> Optional[Any]:
        e = self._entries.get(name)
        return e.config if e is not None else None

    # ----------------------------------------------------------- 连接

    async def connect_all(self, configs: Dict[str, Any]) -> None:
        tasks: List[asyncio.Task] = []
        for name, config in configs.items():
            e = _InternalEntry(
                name=name,
                config=config,
                status="disabled" if _is_disabled(config) else "pending",
            )
            self._entries[name] = e
            self._emit(e)
            if e.status != "disabled":
                tasks.append(asyncio.ensure_future(self._connect_one(e)))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def connect(self, name: str, config: Any) -> None:
        previous = self._entries.get(name)
        if previous is not None and previous.status in ("pending", "connected"):
            return
        disabled = _is_disabled(config)
        e = _InternalEntry(name=name, config=config, status="disabled" if disabled else "pending")
        self._entries[name] = e
        self._emit(e)
        if not disabled:
            await self._connect_one(e)

    async def remove(self, name: str) -> bool:
        e = self._entries.get(name)
        if e is None:
            return False
        await self._close_client(e)
        e.status = "removed"
        e.raw_tools = None
        e.enabled_names = None
        e.client = None
        self._emit(e)
        self._entries.pop(name, None)
        return True

    async def reconnect(self, name: str) -> None:
        e = self._entries.get(name)
        if e is None or e.status == "removed":
            raise McpStartupError(f"未知 MCP server: {name}")
        if _is_disabled(e.config):
            raise McpStartupError(f"MCP server 已禁用: {name}")
        await self._close_client(e)
        e.status = "pending"
        e.raw_tools = None
        e.enabled_names = None
        e.client = None
        self._emit(e)
        await self._connect_one(e)

    async def shutdown(self) -> None:
        entries = list(self._entries.values())
        self._entries.clear()
        await asyncio.gather(
            *(self._close_client(e) for e in entries), return_exceptions=True
        )

    # ----------------------------------------------------------- 内部

    async def _connect_one(self, e: "_InternalEntry") -> None:
        timeout = _cfg_int(e.config, "startupTimeoutMs") or self._default_startup_timeout
        tool_timeout = _cfg_int(e.config, "toolTimeoutMs") or self._default_tool_timeout
        client: Optional[MCPClient] = None
        try:
            client = self._create_client(e.config, timeout, tool_timeout)
            e.client = client
            await asyncio.wait_for(client.connect(), timeout=timeout / 1000.0)
            mcp_tools = await client.list_tools()
            e.raw_tools = mcp_tools
            e.enabled_names = compute_enabled_names(
                mcp_tools,
                _cfg_list(e.config, "enabledTools"),
                _cfg_list(e.config, "disabledTools"),
            )
            e.status = "connected"
            self._watch_unexpected_close(e, client)
        except Exception as error:
            e.status = "failed"
            e.error = _format_startup_error(error, client)
            e.raw_tools = None
            e.enabled_names = None
            await self._close_client(e)
        self._emit(e)

    def _create_client(self, config: Any, startup_timeout: int, tool_timeout: Optional[int]) -> MCPClient:
        transport = getattr(config, "transport", None)
        kwargs = dict(
            startup_timeout_ms=startup_timeout,
            tool_timeout_ms=tool_timeout,
            env_lookup=self._env_lookup,
        )
        if transport == "stdio":
            return StdioMcpClient(config, **kwargs)
        if transport == "sse":
            return SseMcpClient(config, **kwargs)
        return HttpMcpClient(config, **kwargs)

    def _watch_unexpected_close(self, e: "_InternalEntry", client: MCPClient) -> None:
        def _listener(reason: Dict[str, Any]) -> None:
            if e.client is not client:
                return
            e.status = "failed"
            e.error = _format_unexpected_close(e.name, reason)
            e.raw_tools = None
            e.enabled_names = None
            e.client = None
            asyncio.ensure_future(self._close_client(e))
            self._emit(e)

        try:
            client.on_unexpected_close(_listener)
        except Exception:
            pass

    async def _close_client(self, e: "_InternalEntry") -> None:
        client = e.client
        e.client = None
        if client is None:
            return
        try:
            await client.close()
        except Exception:
            pass

    def _emit(self, e: "_InternalEntry") -> None:
        view = _public(e)
        for listener in list(self._listeners):
            try:
                listener(view)
            except Exception:
                pass


# ----------------------------------------------------------------------- 内部数据结构


class McpServerEntry:
    """对外暴露的 server 状态快照。"""

    def __init__(
        self,
        name: str,
        transport: str,
        status: str,
        tool_count: int = 0,
        error: Optional[str] = None,
    ) -> None:
        self.name = name
        self.transport = transport
        self.status = status
        self.tool_count = tool_count
        self.error = error

    def __repr__(self) -> str:  # noqa: D401
        return f"McpServerEntry(name={self.name!r}, status={self.status!r})"


class _InternalEntry:
    def __init__(self, name: str, config: Any, status: str) -> None:
        self.name = name
        self.config = config
        self.status = status
        self.client: Optional[MCPClient] = None
        self.raw_tools: Optional[List[MCPToolDefinition]] = None
        self.enabled_names: Optional[set] = None
        self.error: Optional[str] = None


def _is_disabled(config: Any) -> bool:
    enabled = _cfg_attr(config, "enabled")
    return enabled is False


def _public(e: _InternalEntry) -> McpServerEntry:
    transport = getattr(e.config, "transport", "unknown")
    tool_count = (
        len(e.enabled_names)
        if e.status == "connected" and e.enabled_names is not None
        else 0
    )
    return McpServerEntry(
        name=e.name,
        transport=transport,
        status=e.status,
        tool_count=tool_count,
        error=e.error,
    )


def _cfg_attr(config: Any, key: str) -> Any:
    if isinstance(config, dict):
        return config.get(key)
    return getattr(config, key, None)


def _cfg_int(config: Any, key: str) -> Optional[int]:
    v = _cfg_attr(config, key)
    return int(v) if isinstance(v, (int, float)) else None


def _cfg_list(config: Any, key: str) -> Optional[List[str]]:
    v = _cfg_attr(config, key)
    return list(v) if v is not None else None


def _format_startup_error(error: Exception, client: Optional[MCPClient]) -> str:
    base = str(error) or error.__class__.__name__
    tail = None
    if isinstance(client, StdioMcpClient):
        snap = client.stderr_snapshot()
        if snap:
            tail = snap.strip()
    return f"{base}\nstderr: {tail}" if tail else base


def _format_unexpected_close(name: str, reason: Dict[str, Any]) -> str:
    parts = [f'MCP server "{name}" closed unexpectedly']
    err = reason.get("error")
    if err is not None:
        parts.append(str(err))
    stderr = reason.get("stderr")
    if stderr:
        parts.append(f"stderr: {stderr.strip()}")
    return "\n".join(parts)
