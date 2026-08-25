"""记忆工具插件: 让 Agent 主动记事实、查记忆 (Hermes 自学习能力的一部分)。"""

from __future__ import annotations

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop


def memory_write(ctx: ToolContext, fact: str, section: str = "事实") -> str:
    store = ctx.kernel.require("memory_store")
    line = store.append_memory(fact, section)
    return f"已记住: {line}"


def memory_update_user(ctx: ToolContext, key: str, value: str) -> str:
    store = ctx.kernel.require("memory_store")
    return f"已更新用户画像: {store.update_user(key, value)}"


def memory_search(ctx: ToolContext, query: str, limit: int = 5) -> str:
    store = ctx.kernel.require("memory_store")
    hits = store.search(query, limit=limit)
    if not hits:
        return "没有找到相关记忆。"
    return "\n".join(f"- [{h['kind']}] {h['content'][:300]} (来源: {h['source']})" for h in hits)


class MemoryToolPlugin(Plugin):
    name = "tools.memory"
    requires = ["tool_registry", "memory_store"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="memory_write",
            description="写入一条跨会话长期记忆 (偏好/约定/结论)",
            parameters={
                "type": "object",
                "properties": {
                    "fact": string_prop("事实, 一句话"),
                    "section": string_prop("分类, 默认'事实'"),
                },
                "required": ["fact"],
            },
            handler=memory_write, group="memory", dangerous=True,
        ))
        registry.register(Tool(
            name="memory_update_user",
            description="更新用户画像字段",
            parameters={
                "type": "object",
                "properties": {
                    "key": string_prop("字段名"),
                    "value": string_prop("字段值"),
                },
                "required": ["key", "value"],
            },
            handler=memory_update_user, group="memory", read_only=True,
        ))
        registry.register(Tool(
            name="memory_search",
            description="检索长期记忆/技能/历史",
            parameters={
                "type": "object",
                "properties": {
                    "query": string_prop("关键词"),
                    "limit": {"type": "integer", "description": "条数, 默认 5"},
                },
                "required": ["query"],
            },
            handler=memory_search, group="memory", read_only=True,
        ))
