"""MCP 桥接插件 —— 把远端 MCP Server 暴露的工具注册成本地 Tool。

安全增强:
- 工具调用权限检查 (白名单/黑名单)
- 调用审计日志
- 超时与重试策略
- 调用频率限制
- 敏感参数过滤
- 沙箱隔离执行

传输（融合层，见 multitransport.py）:
- 原生独占 stdio（``command``）server，行为零改变。
- 当 ``fusion.mcp_multitransport`` 开启时，额外接管 ``url``/``http``/``sse`` server，
  复用 kernel 已测试通过的 http/sse 客户端 + 连接管理器（惰性导入，默认不加载）。

配置 (config.yaml):
    mcp:
      enabled: true
      timeout: 30.0
      servers:
        - name: filesystem            # stdio（原生）
          command: npx
          args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
        - name: remote-api           # http/sse（融合层，需 fusion.mcp_multitransport=true）
          transport: http
          url: https://example.com/mcp
    fusion:
      mcp_multitransport: true       # 启用 http/sse 远程传输
      mcp_safe_names: false          # 工具名走碰撞安全命名（>64 截断），默认关以保兼容
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ...core.kernel import Kernel, Plugin
from ...logging_conf import log
from .client import MCPClient, MCPSecurityPolicy


def _is_remote(server_config: Any) -> bool:
    """本地判断是否为远程传输 server（避免在本模块加载时引入融合层）。"""
    if not isinstance(server_config, dict):
        return False
    transport = server_config.get("transport")
    if transport in ("http", "sse"):
        return True
    if "url" in server_config and transport != "stdio":
        return True
    return False


def _build_security_policy(server_config: Dict[str, Any], global_config: Any) -> MCPSecurityPolicy:
    """构建安全策略 (服务器级配置覆盖全局配置)。"""
    security_config = server_config.get("security", {})

    # 白名单
    allowed_tools = None
    if "allowed_tools" in security_config:
        allowed_tools = set(security_config["allowed_tools"])

    # 黑名单
    denied_tools = set(security_config.get("denied_tools", []))

    # 需要确认的工具
    require_confirm_tools = set(security_config.get("require_confirm_tools", []))

    # 频率限制
    max_calls = security_config.get(
        "max_calls_per_minute",
        global_config.get("mcp.security.max_calls_per_minute", 60)
    )

    # 审计日志
    audit_enabled = security_config.get(
        "audit_enabled",
        global_config.get("mcp.security.audit_enabled", True)
    )

    # 沙箱配置
    sandbox_enabled = security_config.get(
        "sandbox",
        global_config.get("mcp.security.sandbox", False)
    )

    return MCPSecurityPolicy(
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        require_confirm_tools=require_confirm_tools,
        max_calls_per_minute=max_calls,
        audit_enabled=audit_enabled,
        sandbox_enabled=bool(sandbox_enabled),
    )


def _build_stdio_clients(config, multitransport_enabled: bool) -> List[MCPClient]:
    """构建 stdio 客户端（仅 ``command`` 类 server）。远程 server 交由融合层处理。"""
    servers = config.get("mcp.servers", []) or []
    clients = []
    for s in servers:
        if not isinstance(s, dict):
            continue
        if _is_remote(s):
            # 远程 server 在无融合层时给出与原生一致的提示，有融合层时静默交给融合层。
            if not multitransport_enabled:
                log.warning("MCP server '%s' 含 url/transport(http|sse) 但已禁用 fusion.mcp_multitransport，跳过", s.get("name", s))
            continue
        if "command" not in s:
            log.warning("MCP server 配置缺少 command, 跳过: %s", s)
            continue

        security_policy = _build_security_policy(s, config)
        clients.append(MCPClient(
            name=str(s.get("name") or s["command"]),
            command=s["command"],
            args=s.get("args", []),
            env=s.get("env", {}),
            timeout=config.get("mcp.timeout", 30.0),
            security_policy=security_policy,
            max_retries=s.get("max_retries", 2),
            retry_delay=s.get("retry_delay", 1.0),
        ))
    return clients


def register_mcp_tool(
    registry: Any,
    server_name: str,
    spec: Dict[str, Any],
    security_policy: Optional[MCPSecurityPolicy],
    call_fn: Callable[[str, Dict[str, Any]], str],
    safe_names: bool = False,
    on_tool: Optional[Callable] = None,
) -> Optional["Tool"]:
    """把单个 MCP 工具注册进原生 ``tool_registry``。

    原生 stdio 与融合层（http/sse）共用此函数，保证安全策略（黑名单/注入扫描/确认）
    与注册语义完全一致。

    ``call_fn(remote_name, kwargs) -> str`` 是实际调用器：原生传 ``client.call_tool``，
    融合层传 ``MultiTransportMCP.call_tool``。

    ``on_tool``: 选填回调, 收到创建后的 ``Tool`` 对象 (供 MCP Tool Search 引擎索引进目录)。
    返回创建后的 Tool (黑名单拦截则返回 None)。
    """
    from ...tools.base import Tool

    remote_name = spec.get("name", "mcp_tool")
    local_name = (
        f"mcp__{server_name}__{remote_name}"
        if not safe_names
        else _safe_qualify(server_name, remote_name)
    )
    schema = spec.get("inputSchema", {"type": "object", "properties": {}})
    desc = spec.get("description", f"MCP 工具 {remote_name} (来自 {server_name})")

    policy = security_policy or MCPSecurityPolicy()

    # 检查工具是否在黑名单中
    allowed, reason = policy.check_tool_allowed(remote_name)
    if not allowed:
        log.info("MCP 工具 '%s' 被安全策略拦截: %s", remote_name, reason)
        return

    # MCP 安全加固: 扫描工具描述中的注入 (GuardFall/TrustFall 防御)
    try:
        from ..mcp.security import get_mcp_security_guard

        mcp_guard = get_mcp_security_guard()
        desc_check = mcp_guard.scan_tool_description(remote_name, desc)
        if not desc_check.safe:
            log.warning(
                "MCP 工具 '%s' 描述检测到 %d 个注入威胁: %s",
                remote_name, desc_check.threat_count,
                [t["description"] for t in desc_check.threats[:3]],
            )
            needs_confirm_override = True
        else:
            needs_confirm_override = False
    except Exception:  # noqa: BLE001
        needs_confirm_override = False

    # 检查是否需要确认
    needs_confirm = policy.requires_confirm(remote_name) or needs_confirm_override

    def handler(_ctx, **kwargs) -> str:
        # MCP 安全加固: 扫描工具参数中的注入
        try:
            from ..mcp.security import get_mcp_security_guard

            mcp_guard = get_mcp_security_guard()
            param_check = mcp_guard.scan_tool_params(remote_name, kwargs)
            if not param_check.safe:
                threats = [t["description"] for t in param_check.threats[:3]]
                return f"[MCP 安全拦截] 工具 {remote_name} 参数检测到注入: {', '.join(threats)}"
        except Exception:  # noqa: BLE001
            pass
        # 如果需要确认, 检查上下文
        if needs_confirm:
            confirm_fn = getattr(_ctx, 'confirm', None)
            if confirm_fn:
                prompt = f"MCP 工具 {remote_name} 请求执行 (来自 {server_name}): {kwargs}"
                if not confirm_fn(prompt):
                    return f"[MCP 已拒绝] 用户取消了 {remote_name} 的执行"
        return call_fn(remote_name, kwargs)

    tool = Tool(
        name=local_name,
        description=desc,
        parameters=schema,
        handler=handler,
        dangerous=needs_confirm,
        group="mcp",
    )
    registry.register(tool)
    if on_tool is not None:
        try:
            on_tool(tool)
        except Exception:  # noqa: BLE001
            log.warning("MCP Tool Search 索引进目录失败: %s", local_name)
    return tool


def _safe_qualify(server_name: str, tool_name: str) -> str:
    """碰撞安全命名（>64 字符做 FNV-1a 截断），惰性复用 kernel 实现。"""
    from ...kernel.mcp.naming import qualify_mcp_tool_name

    return qualify_mcp_tool_name(server_name, tool_name)


class MCPPlugin(Plugin):
    name = "mcp"
    provides = ["mcp_clients"]
    requires = ["tool_registry", "config"]

    def __init__(self) -> None:
        super().__init__()
        self._clients: List[MCPClient] = []
        self._mt: Any = None  # 融合层多传输管理器（仅开关开启时存在）
        self._safe_names: bool = False
        self._engine: Any = None  # MCP Tool Search 引擎 (以下载模式下不为 None)

    def activate(self, kernel: Kernel) -> None:
        config = kernel.require("config")
        if not config.get("mcp.enabled", True):
            return
        registry = kernel.require("tool_registry")

        multitransport_enabled = bool(config.get("fusion.mcp_multitransport", True))
        self._safe_names = bool(config.get("fusion.mcp_safe_names", True))

        # ---- MCP Tool Search 引擎: 上下文降耗索引 (默认按阈值自动启用) ----
        self._engine = self._build_search_engine(config)
        if self._engine is not None:
            kernel.provide("mcp_tool_search", self._engine, owner=self.name)
            self._engine.install(registry)
            registry.tool_search_engine = self._engine

        # ---- 原生 stdio 客户端（行为不变） ----
        self._clients = _build_stdio_clients(config, multitransport_enabled)
        kernel.provide("mcp_clients", self._clients, owner=self.name)
        for client in self._clients:
            try:
                client.start()
                for spec in client.list_tools():
                    self._register_tool(registry, client, spec)
                log.info("MCP server '%s' 已接入, 工具 %d 个", client.name, len(client.list_tools()))
            except Exception as exc:  # noqa: BLE001
                log.error("MCP server '%s' 接入失败: %s", client.name, exc)

        # ---- 融合层：远程 http/sse server（默认关闭） ----
        if multitransport_enabled:
            self._activate_multitransport(config, registry)

    def _build_search_engine(self, config) -> Optional[Any]:
        ts_config = config.get("mcp.tool_search", {}) or {}
        enabled = ts_config.get("enabled")
        if enabled is False:
            return None
        budget = None
        try:
            from ..mcp_tool_search import ContextBudget, MCPToolSearchEngine
            budget = ContextBudget(
                context_window=ts_config.get(
                    "context_window",
                    config.get("mcp.tool_search.context_window", 200_000),
                ),
                threshold=ts_config.get("threshold", 0.10),
            )
            return MCPToolSearchEngine(
                budget=budget,
                top_k=ts_config.get("top_k", 5),
                enabled=enabled,  # None=自动 / True=强制
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("MCP Tool Search 引擎初始化失败: %s", exc)
            return None

    def _activate_multitransport(self, config, registry) -> None:
        servers = config.get("mcp.servers", []) or []
        remote = {str(s.get("name", f"server-{i}")): s
                  for i, s in enumerate(servers) if isinstance(s, dict) and _is_remote(s)}
        if not remote:
            return
        try:
            from .multitransport import MultiTransportMCP

            self._mt = MultiTransportMCP(timeout=config.get("mcp.timeout", 30.0))
            self._mt.start(remote)
            policies = {name: _build_security_policy(cfg, config) for name, cfg in remote.items()}
            self._mt.register_tools(registry, policies, register_mcp_tool)
            for entry in self._mt.status():
                if entry.status == "connected":
                    log.info("MCP 远程 server '%s'(%s) 已接入", entry.name, entry.transport)
                else:
                    log.warning("MCP 远程 server '%s' 接入失败: %s", entry.name, entry.error)
        except Exception as exc:  # noqa: BLE001
            log.error("MCP 融合层（多传输）启动失败: %s", exc)

    def _register_tool(self, registry, client: MCPClient, spec: Dict[str, Any]) -> None:
        register_mcp_tool(
            registry,
            client.name,
            spec,
            client.security_policy,
            lambda n, k: client.call_tool(n, k),
            self._safe_names,
            on_tool=self._engine.add if self._engine is not None else None,
        )

    def deactivate(self, kernel: Kernel) -> None:
        for client in self._clients:
            try:
                client.stop()
            except Exception:  # noqa: BLE001
                pass
        if self._engine is not None:
            try:
                registry = kernel.get("tool_registry")
                if registry is not None:
                    registry.tool_search_engine = None
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
        if self._mt is not None:
            try:
                self._mt.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._mt = None
