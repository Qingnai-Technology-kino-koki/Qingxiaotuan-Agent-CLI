"""MCP 多传输融合层 —— 在原生 stdio MCP 插件之上 graft kernel 的 http/sse 传输 +
连接管理器 + 碰撞安全命名。

设计原则（融合而非替换）:
- 原生 ``MCPPlugin`` 仍独占 ``command``(stdio) 类 server，行为零改变。
- 仅当 config ``fusion.mcp_multitransport`` 开启时，本模块接管 ``url``/``http``/``sse``
  类 server：复用 kernel 已测试通过的异步客户端（不引新依赖），经独立事件循环线程
  暴露为与原生完全一致的同步 ``Tool`` 注册接口。
- 工具名统一走 ``qualify_mcp_tool_name``（>64 字符做 FNV-1a 哈希截断，防模型工具名超限）。

本模块仅在开关开启时被 ``plugin.py`` 惰性导入，默认不加载，不影响冷启动。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Dict, List, Optional

from ...kernel.mcp.config import resolve_config
from ...kernel.mcp.connection_manager import McpConnectionManager
from ...kernel.mcp.naming import qualify_mcp_tool_name
from ...kernel.mcp.types import MCPContentBlock, MCPToolResult


def is_remote_server(cfg: Any) -> bool:
    """判断一个 server 配置是否走远程传输（http/sse）。

    原生插件只处理 ``command``(stdio)；``url``/显式 ``transport: http|sse`` 的交给本层。
    """
    if not isinstance(cfg, dict):
        return False
    transport = cfg.get("transport")
    if transport in ("http", "sse"):
        return True
    if "url" in cfg and transport != "stdio":
        return True
    return False


def format_mcp_tool_result(result: MCPToolResult) -> str:
    """把 kernel 的 ``MCPToolResult`` 文本化（与原生 ``MCPClient._format_result`` 语义一致）。"""
    parts: List[str] = []
    for block in result.content:
        if not isinstance(block, MCPContentBlock):
            parts.append(str(block))
            continue
        if block.type == "text" and block.text:
            parts.append(block.text)
        elif block.type == "resource" and block.resource:
            parts.append(str(block.resource))
        elif block.data:
            parts.append(block.data)
        elif block.text:
            parts.append(block.text)
    text = "\n".join(parts)
    if result.isError:
        return f"[MCP 工具错误] {text}"
    return text


class MultiTransportMCP:
    """多传输 MCP 管理器（同步门面）：背后驱动 kernel 的异步 ``McpConnectionManager``。

    通过一条独立事件循环线程驱动所有远程 server 的握手/调用，对外暴露同步
    ``start`` / ``register_tools`` / ``call_tool`` / ``status`` / ``shutdown``，
    与原生 stdio 客户端接口对齐，使 ``MCPPlugin`` 可无差别地注册工具。
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._manager: Optional[McpConnectionManager] = None

    # ----------------------------------------------------------- 生命周期

    def start(self, servers: Dict[str, Any]) -> None:
        """连接一批远程 server（dict 配置，键为 server 名）。"""
        remote = {name: cfg for name, cfg in servers.items() if is_remote_server(cfg)}
        if not remote:
            return
        resolved = {}
        for name, cfg in remote.items():
            try:
                resolved[name] = resolve_config(cfg)
            except Exception as exc:  # noqa: BLE001 - 配置非法，跳过该 server
                from ...logging_conf import log

                log.warning("MCP 远程 server '%s' 配置非法，跳过: %s", name, exc)
        if not resolved:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._manager = McpConnectionManager()
        fut = asyncio.run_coroutine_threadsafe(
            self._manager.connect_all(resolved), self._loop
        )
        fut.result(self._timeout)

    def shutdown(self) -> None:
        if self._manager is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._manager.shutdown(), self._loop
                ).result(self._timeout)
            except Exception:  # noqa: BLE001
                pass
        self._stop_loop()

    def _stop_loop(self) -> None:
        if self._loop is not None and self._thread is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5.0)
        self._loop = None
        self._thread = None
        self._manager = None

    def _run(self, coro: Any) -> Any:
        if self._loop is None:
            raise RuntimeError("多传输 MCP 未启动")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(self._timeout)

    # ----------------------------------------------------------- 工具注册

    def register_tools(
        self,
        registry: Any,
        security_policies: Dict[str, Any],
        register_fn: Callable[[Any, str, Dict[str, Any], Any, Callable[[str, Dict[str, Any]], str], bool], None],
    ) -> None:
        """把已连接 server 的工具注册进原生 ``tool_registry``（碰撞安全命名）。

        ``register_fn`` 即 ``plugin.register_mcp_tool``（为避免循环依赖，由调用方传入）。
        """
        if self._manager is None:
            return
        for entry in self._manager.list():
            if entry.status != "connected":
                continue
            resolved = self._manager.resolved(entry.name)
            if not resolved:
                continue
            policy = security_policies.get(entry.name)
            for td in resolved["raw_tools"]:
                if resolved["enabled_names"] and td.name not in resolved["enabled_names"]:
                    continue
                register_fn(
                    registry,
                    entry.name,
                    {"name": td.name, "description": td.description, "inputSchema": td.inputSchema},
                    policy,
                    lambda tn, kw, s=entry.name: self.call_tool(s, tn, kw),
                    True,  # 多传输一律使用碰撞安全命名
                )

    # ----------------------------------------------------------- 调用

    def call_tool(self, server: str, tool: str, args: Dict[str, Any]) -> str:
        if self._manager is None:
            return f"[MCP 错误] server '{server}' 未连接"
        resolved = self._manager.resolved(server)
        if resolved is None or resolved["client"] is None:
            return f"[MCP 错误] server '{server}' 未连接"
        try:
            result = self._run(resolved["client"].call_tool(tool, args))
        except Exception as exc:  # noqa: BLE001
            return f"[MCP 错误] {tool}: {exc}"
        if not isinstance(result, MCPToolResult):
            return str(result)
        return format_mcp_tool_result(result)

    # ----------------------------------------------------------- 状态

    def status(self) -> List[Any]:
        if self._manager is None:
            return []
        return list(self._manager.list())

    def reconnect(self, name: str) -> None:
        if self._manager is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._manager.reconnect(name), self._loop
        ).result(self._timeout)
