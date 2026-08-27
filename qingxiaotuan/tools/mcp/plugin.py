"""MCP 桥接插件 —— 把远端 MCP Server 暴露的工具注册成本地 Tool。

配置 (config.yaml):
    mcp:
      servers:
        - name: filesystem
          command: npx
          args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
          env: {}

每个 server 启动一个 MCPClient, list_tools 后把每个工具注册为本地 Tool,
Tool.handler 调用 client.call_tool 转发。这样远端能力对 Agent 完全透明。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.kernel import Kernel, Plugin
from ...logging_conf import log
from .client import MCPClient


def _build_clients(config) -> List[MCPClient]:
    servers = config.get("mcp.servers", []) or []
    clients = []
    for s in servers:
        if not isinstance(s, dict) or "command" not in s:
            log.warning("MCP server 配置缺少 command, 跳过: %s", s)
            continue
        clients.append(MCPClient(
            name=str(s.get("name") or s["command"]),
            command=s["command"],
            args=s.get("args", []),
            env=s.get("env", {}),
            timeout=config.get("mcp.timeout", 30.0),
        ))
    return clients


class MCPPlugin(Plugin):
    name = "mcp"
    provides = ["mcp_clients"]
    requires = ["tool_registry", "config"]

    def __init__(self) -> None:
        super().__init__()
        self._clients: List[MCPClient] = []

    def activate(self, kernel: Kernel) -> None:
        config = kernel.require("config")
        if not config.get("mcp.enabled", True):
            return
        registry = kernel.require("tool_registry")
        self._clients = _build_clients(config)
        kernel.provide("mcp_clients", self._clients, owner=self.name)
        for client in self._clients:
            try:
                client.start()
                for spec in client.list_tools():
                    self._register_tool(registry, client, spec)
                log.info("MCP server '%s' 已接入, 工具 %d 个", client.name, len(client.list_tools()))
            except Exception as exc:  # noqa: BLE001
                log.error("MCP server '%s' 接入失败: %s", client.name, exc)

    @staticmethod
    def _register_tool(registry, client: MCPClient, spec: Dict[str, Any]) -> None:
        from ...tools.base import Tool
        remote_name = spec.get("name", "mcp_tool")
        local_name = f"mcp__{client.name}__{remote_name}"
        schema = spec.get("inputSchema", {"type": "object", "properties": {}})
        desc = spec.get("description", f"MCP 工具 {remote_name} (来自 {client.name})")

        def handler(_ctx, **kwargs) -> str:
            return client.call_tool(remote_name, kwargs)

        registry.register(Tool(
            name=local_name,
            description=desc,
            parameters=schema,
            handler=handler,
            group="mcp",
        ))

    def deactivate(self, kernel: Kernel) -> None:
        for client in self._clients:
            try:
                client.stop()
            except Exception:  # noqa: BLE001
                pass
