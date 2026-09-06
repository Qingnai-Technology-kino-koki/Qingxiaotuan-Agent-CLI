"""OpenAI Responses API provider（/responses + 流式）。Kimi 的主协议路径。

httpx 直连，零额外依赖。沿用 openai_legacy 的 nvidia `prompt_cache_key` 修复。
"""

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
from ._http import build_client, is_nvidia_provider, iter_sse, raise_for_status, strip_prompt_cache_key


def _convert_messages_responses(system_prompt: str, history: List[Message]) -> List[Dict[str, Any]]:
    """把 history（不含 system）转成 Responses API 的 input 项。system 单独走 instructions。"""
    items: List[Dict[str, Any]] = []
    for m in history:
        if m.role == Role.USER:
            content = m.content
            text = content if isinstance(content, str) else "".join(
                p.text or "" for p in content if p.type == "text"
            )
            items.append({"role": "user", "content": [{"type": "input_text", "text": text}]})
        elif m.role == Role.ASSISTANT:
            content = m.content
            text = content if isinstance(content, str) else "".join(
                p.text or "" for p in content if p.type == "text"
            )
            item: Dict[str, Any] = {"role": "assistant", "content": []}
            if text:
                item["content"].append({"type": "output_text", "text": text})
            for tc in m.tool_calls:
                item["content"].append(
                    {
                        "type": "function_call",
                        "name": tc.name,
                        "call_id": tc.id,
                        "arguments": tc.arguments,
                    }
                )
            items.append(item)
        elif m.role == Role.TOOL:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": m.tool_call_id or "",
                    "output": m.content if isinstance(m.content, str) else m.text_content(),
                }
            )
    return items


def _convert_tool_responses(tool: Tool) -> Dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }


class OpenAIResponsesChatProvider:
    """OpenAI Responses 协议适配（走 /responses）。"""

    name = "openai-responses"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str],
        model: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        is_nvidia: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout: float = 120.0,
        connect_timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self.model_name = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._is_nvidia = is_nvidia
        self._extra_headers = extra_headers
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
        body: Dict[str, Any] = {
            "model": self.model_name,
            "input": _convert_messages_responses(system_prompt, history),
            "instructions": system_prompt,
            "temperature": self._temperature,
            "max_output_tokens": options.max_completion_tokens or self._max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = [_convert_tool_responses(t) for t in tools]
        # nvidia 不支持 prompt_cache_key（上游缺陷）→ 发送前剔除；其它 provider 注入
        if options and options.cache_key and not self._is_nvidia:
            body["prompt_cache_key"] = options.cache_key
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
        # ---- 上游 0.29→0.39 缺陷修复：nvidia 剔除 prompt_cache_key ----
        if self._is_nvidia:
            strip_prompt_cache_key(body)

        client = build_client(
            self._base_url,
            self._api_key,
            timeout=self._timeout,
            connect_timeout=self._connect_timeout,
            extra_headers=self._extra_headers,
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
                async with client.stream("POST", "/responses", json=body) as resp:
                    raise_for_status(resp)
                    # 跟踪当前 function_call 项：call_id -> name
                    fn_name: Dict[str, str] = {}
                    async for _event, data_str in iter_sse(resp):
                        try:
                            evt = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        etype = evt.get("type", "")
                        if etype in ("response.created", "response.in_progress"):
                            sm.id = evt.get("response", {}).get("id")
                            if sm.id and callbacks and callbacks.on_trace_id:
                                sm.trace_id = sm.id
                                callbacks.on_trace_id(sm.id)
                        elif etype == "response.output_text.delta":
                            delta = evt.get("delta")
                            if delta:
                                yield StreamedMessagePart(type="text_delta", text=delta)
                        elif etype == "response.output_item.added":
                            item = evt.get("item", {})
                            if item.get("type") == "function_call":
                                fn_name[item.get("id")] = item.get("name", "")
                        elif etype == "response.function_call_arguments.delta":
                            item_id = evt.get("item_id")
                            yield StreamedMessagePart(
                                type="tool_call_part",
                                id=item_id,
                                name=fn_name.get(item_id),
                                arguments_delta=evt.get("delta", ""),
                            )
                        elif etype == "response.completed":
                            resp_obj = evt.get("response", {})
                            usage = resp_obj.get("usage") or {}
                            sm.usage = Usage(
                                input_tokens=usage.get("input_tokens", 0),
                                output_tokens=usage.get("output_tokens", 0),
                                input_cache_read_tokens=(usage.get("input_token_details") or {}).get(
                                    "cached_tokens", 0
                                ),
                            )
                            sm.finish_reason = FinishReason.TOOL_CALLS if resp_obj.get(
                                "output"
                            ) and any(
                                o.get("type") == "function_call" for o in resp_obj["output"]
                            ) else FinishReason.COMPLETED
                            sm.raw_finish_reason = str(sm.finish_reason)
                            yield StreamedMessagePart(type="finish", finish_reason=sm.finish_reason)
            finally:
                await client.aclose()

        sm._aiter = _gen()
        return sm
