"""registry —— 工具注册表：按名称登记并查取可执行工具。

Python 端用模块级自注册即可：``ToolRegistry`` 支持 ``source`` 标记与反注册用的
``disposal`` 回调。另提供模块级 ``default_registry`` 与 ``register_tool`` 便捷入口。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .contract import ExecutableTool, ToolInfo, ToolSource


class ToolRegistry:
    """可执行工具的注册表。"""

    def __init__(self) -> None:
        # name -> (tool, source, disposal)
        self._entries: Dict[str, "RegistryEntry"] = {}

    def register(
        self,
        tool: ExecutableTool,
        source: ToolSource = "builtin",
        disposal: Optional[Callable[[], None]] = None,
    ) -> None:
        self._entries[tool.name] = RegistryEntry(tool=tool, source=source, disposal=disposal)

    def unregister(self, name: str) -> bool:
        entry = self._entries.pop(name, None)
        if entry is None:
            return False
        if entry.disposal is not None:
            try:
                entry.disposal()
            except Exception:  # noqa: BLE001
                pass
        return True

    def resolve(self, name: str) -> Optional[ExecutableTool]:
        entry = self._entries.get(name)
        return entry.tool if entry is not None else None

    def list(self) -> List[ToolInfo]:
        return [
            ToolInfo(
                name=e.tool.name,
                source=e.source,
                description=getattr(e.tool, "description", ""),
                parameters=getattr(e.tool, "parameters", None),
            )
            for e in self._entries.values()
        ]

    def names(self) -> List[str]:
        return list(self._entries.keys())


class RegistryEntry:
    __slots__ = ("tool", "source", "disposal")

    def __init__(
        self,
        tool: ExecutableTool,
        source: ToolSource,
        disposal: Optional[Callable[[], None]],
    ) -> None:
        self.tool = tool
        self.source = source
        self.disposal = disposal


# --------------------------------------------------- 模块级便捷注册表

default_registry = ToolRegistry()


def register_tool(
    tool: ExecutableTool,
    source: ToolSource = "builtin",
    disposal: Optional[Callable[[], None]] = None,
) -> None:
    """把工具注册进模块级默认注册表（供自注册风格使用）。"""
    default_registry.register(tool, source=source, disposal=disposal)


__all__ = ["ToolRegistry", "RegistryEntry", "default_registry", "register_tool"]
