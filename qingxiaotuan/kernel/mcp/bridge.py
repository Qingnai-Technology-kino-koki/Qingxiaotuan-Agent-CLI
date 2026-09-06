"""桥接到现有 ``qingxiaotuan/tools/mcp`` 体系 —— 把 kernel MCP 工具适配为 ``tools.base.Tool``。

现有 ``tools/mcp`` 通过 ``qingxiaotuan.tools.base.ToolRegistry`` 暴露工具给 CLI，
因此本模块的适配目标是：**产出与现有 ``Tool`` 接口兼容的对象**，使 CLI 可以像注册
本地工具一样注册 MCP 工具（无需改动 ``tools/mcp`` 内部实现）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, List

from ..contract import ContentPart
from .naming import qualify_mcp_tool_name
from .tool import create_mcp_tool
from .types import ExecutableTool, ExecutableToolContext, MCPClient, MCPToolDefinition


def _stringify_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts: List[str] = []
        for p in output:
            if isinstance(p, ContentPart):
                if p.type == "text":
                    parts.append(p.text or "")
                elif p.type == "image_url":
                    url = (p.image_url or {}).get("url", "")
                    parts.append(f"[image: {url[:48]}…]" if url else "[image]")
                elif p.type == "input_audio":
                    parts.append("[audio]")
                else:
                    parts.append(str(p))
            else:
                parts.append(str(p))
        return "\n".join(parts)
    return str(output)


def _run_async(coro: Any) -> Any:
    """在同步 handler 中运行协程：无运行中的事件循环用 asyncio.run；否则丢到独立线程。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def bridge_executable_to_base_tool(exe: ExecutableTool):
    """把一个 kernel ``ExecutableTool`` 适配成 ``tools.base.Tool``。"""
    from ...tools.base import Tool as BaseTool

    def handler(ctx: Any, **kwargs: Any) -> str:
        async def _call() -> str:
            ectx = ExecutableToolContext(signal=getattr(ctx, "signal", None))
            execution = exe.resolve_execution(kwargs)
            result = await execution["execute"](ectx)
            return _stringify_output(result.get("output"))

        return _run_async(_call())

    return BaseTool(
        name=exe.name,
        description=exe.description,
        parameters=exe.parameters,
        handler=handler,
        group="mcp",
    )


def bridge_to_existing_mcp(manager: Any, registry: Any = None):
    """把连接管理器的状态变化桥接到现有 ``ToolRegistry``。

    当某 server 变为 connected，自动把其 MCP 工具注册进 registry（适配为 BaseTool）；
    disabled/failed/removed/needs-auth 时反注册。返回内部 ``McpToolRegistry``，便于查询。
    """
    from .registry_integration import McpToolRegistry

    mcp_reg = McpToolRegistry(registry)
    mcp_reg.attach_to(manager)
    return mcp_reg


def get_all_mcp_tools(manager: Any) -> List[Any]:
    """收集当前所有已连接 server 的 MCP 工具，适配为 ``tools.base.Tool`` 便于 CLI 注册。"""
    from ...tools.base import Tool as BaseTool

    tools: List[Any] = []
    for entry in manager.list():
        if entry.status != "connected":
            continue
        resolved = manager.resolved(entry.name)
        if not resolved:
            continue
        for td in resolved["raw_tools"]:
            if resolved["enabled_names"] and td.name not in resolved["enabled_names"]:
                continue
            exe = create_mcp_tool(
                qualify_mcp_tool_name(entry.name, td.name), td, resolved["client"]
            )
            tools.append(bridge_executable_to_base_tool(exe))
    return tools
