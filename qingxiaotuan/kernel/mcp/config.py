"""MCP server 配置解析。

无 transport 时按 ``command`` -> stdio / ``url`` -> http 自动补全。
使用 pydantic（可选依赖）做判别联合校验；无 pydantic 时降级为 dict 校验。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from .errors import McpStartupError

DEFAULT_STARTUP_TIMEOUT_MS = 30_000
DEFAULT_TOOL_TIMEOUT_MS = 2_147_483_647  # 对应 MAX_MCP_TIMEOUT_MS，实际交给客户端默认

# ---------------------------------------------------------------------------
# pydantic 可选依赖: 无 pydantic 时跳过模型校验, 仅做 dict 级验证
# ---------------------------------------------------------------------------
try:
    from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
    from typing_extensions import Annotated, Literal
    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False
    BaseModel = None  # type: ignore[assignment,misc]
    ConfigDict = None  # type: ignore[assignment,misc]
    Field = None  # type: ignore[assignment,misc]
    TypeAdapter = None  # type: ignore[assignment,misc]
    field_validator = None  # type: ignore[assignment,misc]
    Annotated = None  # type: ignore[assignment,misc]
    Literal = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Pydantic models (仅在 pydantic 可用时定义)
# ---------------------------------------------------------------------------
_MCP_SERVER_CONFIG: Any = None  # Will hold TypeAdapter or None

if _HAS_PYDANTIC:
    class _CommonFields(BaseModel):  # type: ignore[misc]
        model_config = ConfigDict(extra="allow")  # type: ignore[union-attr]
        enabled: Optional[bool] = None
        startupTimeoutMs: Optional[int] = None
        toolTimeoutMs: Optional[int] = None
        enabledTools: Optional[List[str]] = None
        disabledTools: Optional[List[str]] = None

    class McpServerStdioConfig(_CommonFields):  # type: ignore[misc]
        transport: Literal["stdio"] = "stdio"  # type: ignore[assignment]
        command: str
        args: Optional[List[str]] = None
        env: Optional[Dict[str, str]] = None
        cwd: Optional[str] = None
        executor: Optional[str] = None
        runtime_id: Optional[str] = None

    class McpServerRemoteConfig(_CommonFields):  # type: ignore[misc]
        url: str
        headers: Optional[Dict[str, str]] = None
        auth: Optional[Literal["oauth"]] = None  # type: ignore[assignment]
        bearerTokenEnvVar: Optional[str] = None

    class McpServerHttpConfig(McpServerRemoteConfig):  # type: ignore[misc]
        transport: Literal["http"] = "http"  # type: ignore[assignment]

    class McpServerSseConfig(McpServerRemoteConfig):  # type: ignore[misc]
        transport: Literal["sse"] = "sse"  # type: ignore[assignment]

    McpServerConfig = Annotated[  # type: ignore[misc]
        Union[McpServerStdioConfig, McpServerHttpConfig, McpServerSseConfig],
        Field(discriminator="transport"),  # type: ignore[union-attr]
    ]

    _MCP_SERVER_CONFIG = TypeAdapter(McpServerConfig)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_config(raw: Any) -> Any:
    """补全 transport 并校验为 ``McpServerConfig``。

    - 已带 ``transport``：直接校验。
    - 否则按 ``command`` -> stdio / ``url`` -> http 推断。
    - 无 pydantic 时跳过校验，仅做 transport 推断。
    """
    if not isinstance(raw, dict):
        raise McpStartupError(f"MCP server config 必须是对象，收到 {type(raw).__name__}")
    raw = dict(raw)
    if "transport" not in raw:
        if isinstance(raw.get("command"), str):
            raw["transport"] = "stdio"
        elif isinstance(raw.get("url"), str):
            raw["transport"] = "http"
        else:
            raise McpStartupError(
                "MCP server config 必须包含 transport，或 command（stdio）/ url（http）之一"
            )
    if _MCP_SERVER_CONFIG is not None:
        return _MCP_SERVER_CONFIG.validate_python(raw)
    # No pydantic: do basic dict validation
    transport = raw.get("transport")
    if transport == "stdio" and not isinstance(raw.get("command"), str):
        raise McpStartupError("stdio 配置必须包含 command 字段")
    if transport in ("http", "sse") and not isinstance(raw.get("url"), str):
        raise McpStartupError(f"{transport} 配置必须包含 url 字段")
    return raw


def load_mcp_config(path_or_dict: Any) -> Dict[str, Any]:
    """解析 ``{mcpServers: {...}}``（或扁平 ``{name: config}``）为 ``name -> McpServerConfig``。

    接受：
    - dict：直接当作配置（含顶层 ``mcpServers`` 则取其内部）。
    - str / Path：JSON 文件路径。
    """
    if isinstance(path_or_dict, (str, Path)):
        with open(path_or_dict, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    elif isinstance(path_or_dict, dict):
        data = path_or_dict
    else:
        raise TypeError("load_mcp_config 接受 dict 或文件路径")

    if not isinstance(data, dict):
        raise McpStartupError("MCP 配置根必须是对象")
    servers = data.get("mcpServers", data)
    if not isinstance(servers, dict):
        raise McpStartupError("MCP servers 必须是对象")

    out: Dict[str, Any] = {}
    for name, cfg in servers.items():
        out[name] = resolve_config(cfg)
    return out


def merge_mcp_configs(*configs: Dict[str, Any]) -> Dict[str, Any]:
    """多层配置合并（后者覆盖前者，简化为单层合并）。"""
    merged: Dict[str, Any] = {}
    for cfg in configs:
        if not cfg:
            continue
        merged.update(cfg)
    return merged


def compute_enabled_names(
    tools: List[Any],
    enabled_tools: Optional[List[str]] = None,
    disabled_tools: Optional[List[str]] = None,
) -> set:
    """根据 enabled/disabled 过滤，返回实际启用的工具名集合。"""
    names = [t.name if hasattr(t, "name") else t for t in tools]
    enabled = set(names)
    if enabled_tools is not None:
        ef = set(enabled_tools)
        enabled = {n for n in names if n in ef}
    if disabled_tools is not None:
        df = set(disabled_tools)
        enabled = {n for n in enabled if n not in df}
    return enabled


def is_remote_config(config: Any) -> bool:
    if isinstance(config, dict):
        return config.get("transport") in ("http", "sse")
    transport = getattr(config, "transport", None)
    return transport in ("http", "sse")


def build_remote_headers(config: Any, env_lookup: Optional[Callable[[str], str]] = None) -> Optional[Dict[str, str]]:
    """构造远程（http/sse）请求头，注入 bearer token。"""
    env_lookup = env_lookup or (lambda n: os.environ.get(n))
    headers: Dict[str, str] = {}
    cfg_headers = getattr(config, "headers", None) or {}
    headers.update(cfg_headers)
    token_env = getattr(config, "bearerTokenEnvVar", None)
    if token_env is not None:
        token = env_lookup(token_env)
        if not token:
            raise McpStartupError(
                f"MCP {getattr(config, 'transport', 'remote')} bearer token 环境变量 "
                f"'{token_env}' 未设置或为空"
            )
        headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
        headers["Authorization"] = f"Bearer {token}"
    return headers or None
