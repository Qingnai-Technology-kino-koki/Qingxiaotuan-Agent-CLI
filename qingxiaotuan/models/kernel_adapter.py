"""KernelModelAdapter —— 把现有 ModelAdapter 接口桥接到 kernel ChatProvider。

目的：让 core/agent.py 的 Agent 与 cli/cmd_chat.py 在不改动的前提下，用上 Kimi 风格的
kernel provider 架构（统一 ChatProvider 接口 + 顶层 generate 聚合器 + ProviderService）。

接口契约（来自 models/base.ModelAdapter）：
    chat(messages, tools=None, stream=False, on_token=None, on_reason=None) -> ModelResponse

其中 messages 是 OpenAI 风格 dict 列表（role/content/tool_calls/tool_call_id），
tools 是 OpenAI 风格工具声明列表（含 function.{name,description,parameters}）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from ..kernel.contract import (
    GenerateCallbacks,
    GenerateOptions,
    Message,
    Role,
    Tool,
    ToolCall,
    image_url_part,
    text_part,
)
from ..kernel import generate
from ..kernel.provider_service import ProviderConfig, build_provider
from ..models.base import ModelAdapter, ModelCapabilities, ModelResponse, ToolCall as _ToolCall


def _to_kernel_message(d: Dict[str, Any]) -> Message:
    role = Role(d.get("role", "user"))
    content = d.get("content", "")
    if isinstance(content, list):
        parts = []
        for p in content:
            ptype = p.get("type")
            if ptype == "text":
                parts.append(text_part(p.get("text", "")))
            elif ptype == "image_url" and isinstance(p.get("image_url"), dict):
                parts.append(image_url_part(p["image_url"].get("url", "")))
        content = parts or ""
    tcs: List[ToolCall] = []
    for tc in d.get("tool_calls", []) or []:
        fn = tc.get("function", {})
        if isinstance(fn, dict):
            tcs.append(
                ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=fn.get("arguments", ""))
            )
        else:
            tcs.append(ToolCall(id=tc.get("id", ""), name=str(tc.get("name", "")), arguments=""))
    return Message(
        role=role,
        content=content,
        tool_calls=tcs,
        tool_call_id=d.get("tool_call_id"),
        name=d.get("name"),
    )


def _to_kernel_tool(t: Dict[str, Any]) -> Tool:
    if "function" in t and isinstance(t["function"], dict):
        f = t["function"]
        return Tool(name=f.get("name", ""), description=f.get("description", ""), parameters=f.get("parameters", {}))
    return Tool(
        name=t.get("name", ""),
        description=t.get("description", ""),
        parameters=t.get("parameters", t.get("input_schema", {})),
    )


class KernelModelAdapter(ModelAdapter):
    """委托给一个 kernel ChatProvider 的 ModelAdapter 实现。"""

    def __init__(self, provider, model: Optional[str] = None) -> None:
        self._provider = provider
        self.name = getattr(provider, "name", "kernel")
        self.model_name = getattr(provider, "model_name", model or "")
        self.capabilities = ModelCapabilities(
            tool_calling=True, function_calling=True, streaming=True,
            vision=getattr(provider, "supports_vision", False),
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        on_reason: Optional[Callable[[str], None]] = None,
    ) -> ModelResponse:
        # 拆分 system / history（kernel 的 system 单独传）
        system_prompt = ""
        history: List[Message] = []
        for m in messages:
            if m.get("role") == "system" and not system_prompt:
                system_prompt = m.get("content", "") if isinstance(m.get("content"), str) else ""
            else:
                history.append(_to_kernel_message(m))

        kt = [_to_kernel_tool(t) for t in (tools or [])]
        callbacks = GenerateCallbacks(on_token=on_token, on_reasoning=on_reason)
        options = GenerateOptions()

        result = asyncio.run(
            generate(self._provider, system_prompt, kt, history, callbacks, options)
        )

        msg = result.message
        content = msg.content if isinstance(msg.content, str) else msg.text_content()
        tool_calls = [
            _ToolCall(id=tc.id, name=tc.name, arguments=tc.arguments) for tc in msg.tool_calls
        ]
        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=str(result.finish_reason),
            usage=result.usage.to_dict(),
            reasoning="",
        )


def create_kernel_adapter(config) -> KernelModelAdapter:
    """从青小团 Config 构造 KernelModelAdapter（统一入口开关：model.backend=kernel）。"""
    provider_cfg = ProviderConfig(
        type=config.get("model.provider", "openai"),
        model=config.get("model.model", ""),
        base_url=config.get("model.base_url"),
        api_key=config.api_key(),
        api_key_env=config.get("model.api_key_env"),
        protocol=config.get("model.kernel_protocol"),  # 可选：openai / openai_responses / anthropic
        max_tokens=config.get("model.max_tokens", 8192),
        temperature=config.get("model.temperature", 0.7),
    )
    provider = build_provider(provider_cfg)
    return KernelModelAdapter(provider, model=provider_cfg.model)
