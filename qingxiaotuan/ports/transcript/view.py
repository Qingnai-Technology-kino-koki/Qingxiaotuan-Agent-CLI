"""View registry for tool/input/marker renderers (对齐 view/registry.ts 接口)."""

from __future__ import annotations

from typing import Generic, Optional, TypeVar

from .model import ToolCallFrame

C = TypeVar("C")


class ViewRegistry(Generic[C]):
    def __init__(self, options: Optional[dict] = None):
        options = options or {}
        self._tool_renderers: dict = {}
        self._input_renderers: dict = {}
        self._marker_renderers: dict = {}
        self._fallback_tool: Optional[C] = options.get("fallbackTool")

    def register_tool(self, key: str, renderer: C) -> "ViewRegistry[C]":
        self._tool_renderers[key.lower()] = renderer
        return self

    def register_input(self, origin_kind: str, renderer: C) -> "ViewRegistry[C]":
        self._input_renderers[origin_kind] = renderer
        return self

    def register_marker(self, marker: str, renderer: C) -> "ViewRegistry[C]":
        self._marker_renderers[marker] = renderer
        return self

    def resolve_tool(self, frame: ToolCallFrame) -> Optional[C]:
        key = (frame.view or frame.name).lower()
        return self._tool_renderers.get(key, self._fallback_tool)

    def resolve_input(self, origin: dict) -> Optional[C]:
        return self._input_renderers.get((origin or {}).get("kind"))

    def resolve_marker(self, marker: str) -> Optional[C]:
        return self._marker_renderers.get(marker)
