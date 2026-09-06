"""OpenAI Chat Completions provider（chat/completions + tools + 流式）。

覆盖所有 OpenAI 兼容端点：OpenAI、DeepSeek、Kimi、通义、GLM、本地网关(Ollama/vLLM) 等。
httpx 直连，零额外依赖。

上游缺陷修复：
- nvidia 系 provider 在请求发出前剔除 `prompt_cache_key`（is_nvidia 构造参数控制）。
- 空值保护：_http.strip_prompt_cache_key 在 body 为空/非 dict/字段缺失时直接跳过。
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


def _convert_tool(tool: Tool) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _convert_messages(system_prompt: str, history: List[Message]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})
    for m in history:
        out.append(m.to_dict())
    return out


def _map_finish_reason(raw: Optional[str]) -> FinishReason:
    if raw is None:
        return FinishReason.OTHER
    return {
        "stop": FinishReason.COMPLETED,
        "tool_calls": FinishReason.TOOL_CALLS,
        "length": FinishReason.TRUNCATED,
        "content_filter": FinishReason.FILTERED,
    }.get(raw, FinishReason.OTHER)


class OpenAILegacyChatProvider:
    """OpenAI chat/completions 协议适配。"""

    name = "openai-legacy"

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
            "messages": _convert_messages(system_prompt, history),
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = [_convert_tool(t) for t in tools]
            body["tool_choice"] = "auto"
        if options and options.max_completion_tokens:
            body["max_tokens"] = options.max_completion_tokens
        # prompt 缓存键：nvidia 不支持 → 由下方 strip 处理；其它 provider 注入
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
                async with client.stream(
                    "POST", "/chat/completions", json=body
                ) as resp:
                    raise_for_status(resp)
                    async for _event, data_str in iter_sse(resp):
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if callbacks and callbacks.on_trace_id and chunk.get("id") and not sm.trace_id:
                            sm.trace_id = chunk.get("id")
                            callbacks.on_trace_id(chunk["id"])
                        choices = chunk.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            if delta.get("content"):
                                yield StreamedMessagePart(type="text_delta", text=delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                yield StreamedMessagePart(
                                    type="tool_call_part",
                                    id=tc.get("id"),
                                    name=(tc.get("function") or {}).get("name"),
                                    arguments_delta=(tc.get("function") or {}).get("arguments", ""),
                                    index=tc.get("index"),
                                )
                            raw_fr = choices[0].get("finish_reason")
                            if raw_fr:
                                sm.raw_finish_reason = raw_fr
                                sm.finish_reason = _map_finish_reason(raw_fr)
                        if chunk.get("usage"):
                            u = chunk["usage"]
                            sm.usage = Usage(
                                input_tokens=u.get("prompt_tokens", 0),
                                output_tokens=u.get("completion_tokens", 0),
                                input_cache_read_tokens=u.get("prompt_tokens_details", {}).get(
                                    "cached_tokens", 0
                                )
                                if isinstance(u.get("prompt_tokens_details"), dict)
                                else 0,
                            )
                        if sm.finish_reason and sm.usage:
                            yield StreamedMessagePart(type="finish", finish_reason=sm.finish_reason)
            finally:
                await client.aclose()

        sm._aiter = _gen()
        return sm
