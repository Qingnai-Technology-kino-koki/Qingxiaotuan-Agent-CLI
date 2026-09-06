"""Anthropic Messages API provider（/v1/messages + 流式，httpx 直连）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..contract import (
    FinishReason,
    GenerateOptions,
    Message,
    Role,
    StreamedMessage,
    StreamedMessagePart,
    Tool,
    Usage,
)
from ._http import build_client, iter_sse, raise_for_status, strip_prompt_cache_key


def _convert_messages_anthropic(system_prompt: str, history: List[Message]) -> Dict[str, Any]:
    """返回 {"system": ..., "messages": [...]}。Anthropic 的 system 是独立顶层字段。"""
    messages: List[Dict[str, Any]] = []
    for m in history:
        if m.role == Role.TOOL:
            # Anthropic: 工具结果放在 user 消息的 tool_result 块
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id or "",
                            "content": m.content if isinstance(m.content, str) else m.text_content(),
                        }
                    ],
                }
            )
        elif m.role == Role.USER:
            content = m.content
            if isinstance(content, str):
                messages.append({"role": "user", "content": content})
            else:
                blocks = []
                for p in content:
                    if p.type == "text":
                        blocks.append({"type": "text", "text": p.text or ""})
                    elif p.type == "image_url":
                        blocks.append(
                            {"type": "image", "source": {"type": "base64", **(p.image_url or {})}}
                        )
                messages.append({"role": "user", "content": blocks})
        elif m.role == Role.ASSISTANT:
            content = m.content
            text = content if isinstance(content, str) else "".join(
                p.text or "" for p in content if p.type == "text"
            )
            blocks: List[Dict[str, Any]] = []
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"__raw": tc.arguments}
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": args}
                )
            messages.append({"role": "assistant", "content": blocks})
    return {"system": system_prompt, "messages": messages}


def _convert_tools_anthropic(tools: List[Tool]) -> List[Dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in tools
    ]


def _map_stop_reason(raw: Optional[str]) -> FinishReason:
    return {
        "end_turn": FinishReason.COMPLETED,
        "tool_use": FinishReason.TOOL_CALLS,
        "max_tokens": FinishReason.TRUNCATED,
        "stop_sequence": FinishReason.COMPLETED,
    }.get(raw or "", FinishReason.OTHER)


class AnthropicChatProvider:
    """Anthropic Messages 协议适配。"""

    name = "anthropic"

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com/v1",
        api_key: Optional[str],
        model: str = "claude-3-5-sonnet-latest",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout: float = 120.0,
        connect_timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self.model_name = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._extra_headers = extra_headers or {}
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self.thinking_effort = None
        self.max_completion_tokens = max_tokens

    def _build_body(
        self,
        system_prompt: str,
        tools: List[Tool],
        history: List[Message],
        options: Optional[GenerateOptions],
    ) -> Dict[str, Any]:
        conv = _convert_messages_anthropic(system_prompt, history)
        body: Dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": options.max_completion_tokens or self._max_tokens,
            "temperature": self._temperature,
            "stream": True,
            "messages": conv["messages"],
        }
        if conv["system"]:
            body["system"] = conv["system"]
        if tools:
            body["tools"] = _convert_tools_anthropic(tools)
        # Anthropic 不支持 prompt_cache_key（其缓存走 cache_control），防御性剔除（空值保护）
        strip_prompt_cache_key(body)
        return body

    async def generate(
        self,
        system_prompt: str,
        tools: List[Tool],
        history: List[Message],
        options: Optional[GenerateOptions] = None,
        callbacks: Optional[Any] = None,
    ) -> StreamedMessage:
        body = self._build_body(system_prompt, tools, history, options)
        headers = {
            "x-api-key": self._api_key or "",
            "anthropic-version": "2023-06-01",
            **self._extra_headers,
        }
        client = build_client(
            self._base_url,
            None,  # Anthropic 用 x-api-key，不走 Bearer
            timeout=self._timeout,
            connect_timeout=self._connect_timeout,
            extra_headers=headers,
        )

        sm = StreamedMessage.__new__(StreamedMessage)
        sm.id = None
        sm.usage = None
        sm.finish_reason = None
        sm.raw_finish_reason = None
        sm.trace_id = None
        sm.request_id = None

        async def _gen() -> None:
            try:
                async with client.stream("POST", "/messages", json=body) as resp:
                    raise_for_status(resp)
                    tool_blocks: Dict[str, Dict[str, Any]] = {}
                    async for _event, data_str in iter_sse(resp):
                        try:
                            evt = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        etype = evt.get("type", "")
                        if etype == "message_start":
                            u = (evt.get("message") or {}).get("usage") or {}
                            sm.usage = Usage(input_tokens=u.get("input_tokens", 0))
                            sm.id = evt.get("message", {}).get("id")
                            if sm.id and callbacks and callbacks.on_trace_id:
                                sm.trace_id = sm.id
                                callbacks.on_trace_id(sm.id)
                        elif etype == "content_block_start":
                            block = evt.get("content_block", {})
                            if block.get("type") == "tool_use":
                                tool_blocks[block.get("id")] = {
                                    "name": block.get("name", ""),
                                    "input_json": "",
                                }
                        elif etype == "content_block_delta":
                            delta = evt.get("delta", {})
                            dt = delta.get("type")
                            if dt == "text_delta":
                                yield StreamedMessagePart(type="text_delta", text=delta.get("text", ""))
                            elif dt == "thinking_delta":
                                yield StreamedMessagePart(
                                    type="thinking_delta", text=delta.get("thinking", "")
                                )
                            elif dt == "input_json_delta":
                                bid = evt.get("content_block")
                                blk = tool_blocks.get(bid)
                                if blk is not None:
                                    blk["input_json"] += delta.get("partial_json", "")
                                    yield StreamedMessagePart(
                                        type="tool_call_part",
                                        id=bid,
                                        name=blk["name"],
                                        arguments_delta=delta.get("partial_json", ""),
                                    )
                        elif etype == "message_delta":
                            u = evt.get("usage") or {}
                            if sm.usage:
                                sm.usage.output_tokens = u.get("output_tokens", 0)
                            sm.raw_finish_reason = evt.get("stop_reason")
                            sm.finish_reason = _map_stop_reason(evt.get("stop_reason"))
                        elif etype == "message_stop":
                            if sm.finish_reason:
                                yield StreamedMessagePart(type="finish", finish_reason=sm.finish_reason)
            finally:
                await client.aclose()

        sm._aiter = _gen()
        return sm
