"""模型适配器接口。任何新模型只要实现这个接口即可接入青小团。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class ModelCapabilities:
    """模型能力矩阵：适配器和上层编排据此决定可用功能。"""

    tool_calling: bool = False
    function_calling: bool = False
    streaming: bool = False
    vision: bool = False
    json_mode: bool = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON 字符串 (与 OpenAI 协议一致)


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Dict[str, int] = field(default_factory=dict)
    reasoning: str = ""  # deepseek-reasoner 等模型的思维链


class ModelAdapter:
    """模型适配器基类。"""

    name: str = "base"
    capabilities = ModelCapabilities()

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        on_reason: Optional[Callable[[str], None]] = None,
    ) -> ModelResponse:
        raise NotImplementedError
