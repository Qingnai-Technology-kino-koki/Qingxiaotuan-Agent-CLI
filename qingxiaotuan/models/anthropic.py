"""Anthropic Messages API adapter."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import httpx

from .base import ModelAdapter, ModelCapabilities, ModelResponse, ToolCall


class AnthropicAdapter(ModelAdapter):
    name = "anthropic"
    capabilities = ModelCapabilities(
        tool_calling=True, function_calling=True, streaming=True,
        vision=True, json_mode=False,
    )

    def __init__(self, base_url: str, model: str, api_key: Optional[str],
                 temperature: float = 0.7, max_tokens: int = 8192,
                 timeout: float = 120,
                 connect_timeout: float = 10.0, read_timeout: float = 120.0,
                 prompt_cache: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.prompt_cache = prompt_cache
        self.timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout or timeout,
                                     write=connect_timeout, pool=connect_timeout)

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
             stream: bool = False, on_token: Optional[Callable[[str], None]] = None,
             on_reason: Optional[Callable[[str], None]] = None) -> ModelResponse:
        if not self.api_key:
            raise RuntimeError("未配置 Anthropic API Key，请设置 ANTHROPIC_API_KEY。")
        system, converted = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "model": self.model, "system": system, "messages": converted,
            "max_tokens": self.max_tokens, "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = [self._convert_tool(tool) for tool in tools]
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        with httpx.Client(timeout=self.timeout) as client:
            if stream:
                return self._stream(client, f"{self.base_url}/messages", headers,
                                    payload, on_token)
            response = client.post(f"{self.base_url}/messages", headers=headers, json=payload)
            response.raise_for_status()
            return self._parse(response.json())

    @classmethod
    def _convert_messages(cls, messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
        system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        converted: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "tool":
                raw = message.get("content", "")
                if isinstance(raw, list):
                    blocks = cls._convert_content_blocks(raw)
                    converted.append({"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": message.get("tool_call_id", ""),
                        "content": blocks,
                    }]})
                else:
                    converted.append({"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": message.get("tool_call_id", ""),
                        "content": raw,
                    }]})
                continue
            if role == "assistant" and message.get("tool_calls"):
                assistant_blocks: List[Dict[str, Any]] = []
                if message.get("content"):
                    assistant_blocks.append({"type": "text", "text": message["content"]})
                for call in message["tool_calls"]:
                    fn = call.get("function", {})
                    try:
                        arguments = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        arguments = {}
                    assistant_blocks.append({"type": "tool_use", "id": call.get("id", ""),
                                             "name": fn.get("name", ""), "input": arguments})
                converted.append({"role": "assistant", "content": assistant_blocks})
                continue
            raw = message.get("content", "")
            if isinstance(raw, list):
                # 多模态 content (text + image_url 块) -> 翻译为 Anthropic 块
                converted.append({"role": "assistant" if role == "assistant" else "user",
                                  "content": cls._convert_content_blocks(raw)})
            else:
                converted.append({"role": "assistant" if role == "assistant" else "user",
                                  "content": raw})
        return system, converted

    @staticmethod
    def _convert_content_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把 OpenAI 风格 content 块翻译为 Anthropic 块。

        image_url 数据 URI -> Anthropic image source (base64);
        远程 URL -> Anthropic image source (url); 其余块原样保留。
        """
        out: List[Dict[str, Any]] = []
        for b in blocks:
            if b.get("type") == "image_url":
                url = b["image_url"]["url"]
                if url.startswith("data:"):
                    meta, b64 = url[5:].split(",", 1)
                    mt = meta.split(";", 1)[0] or "image/png"
                    out.append({"type": "image", "source": {
                        "type": "base64", "media_type": mt, "data": b64}})
                else:
                    out.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                out.append(b)
        return out

    @staticmethod
    def _convert_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
        function = tool.get("function", {})
        return {"name": function.get("name", ""),
                "description": function.get("description", ""),
                "input_schema": function.get("parameters", {"type": "object", "properties": {}})}

    @staticmethod
    def _parse(data: Dict[str, Any]) -> ModelResponse:
        content = data.get("content", []) or []
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        calls = [ToolCall(id=b.get("id", ""), name=b.get("name", ""),
                          arguments=json.dumps(b.get("input", {}), ensure_ascii=False))
                 for b in content if b.get("type") == "tool_use"]
        usage = data.get("usage", {}) or {}
        return ModelResponse(content=text, tool_calls=calls,
                             finish_reason=data.get("stop_reason", "stop"),
                             usage={k: int(v) for k, v in usage.items()
                                    if isinstance(v, (int, float))})

    def _stream(self, client, url: str, headers: Dict[str, str], payload: Dict[str, Any],
                on_token: Optional[Callable[[str], None]]) -> ModelResponse:
        payload["stream"] = True
        parts: List[str] = []
        calls: List[ToolCall] = []
        tool_id = ""
        tool_name = ""
        tool_input = ""
        usage: Dict[str, int] = {}
        with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            event = ""
            data_lines: List[str] = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line and data_lines:
                    data = json.loads("\n".join(data_lines))
                    data_lines = []
                    if event == "content_block_delta":
                        delta = data.get("delta", {}) or {}
                        value = delta.get("text", "")
                        if value:
                            parts.append(value)
                            if on_token:
                                on_token(value)
                        if delta.get("type") == "input_json_delta":
                            tool_input += delta.get("partial_json", "")
                    elif event == "content_block_start":
                        block = data.get("content_block", {}) or {}
                        if block.get("type") == "tool_use":
                            tool_id = block.get("id", "")
                            tool_name = block.get("name", "")
                            tool_input = json.dumps(block.get("input", {}), ensure_ascii=False)
                    elif event == "content_block_stop" and tool_name:
                        calls.append(ToolCall(id=tool_id, name=tool_name,
                                              arguments=tool_input or "{}"))
                        tool_id = tool_name = tool_input = ""
                    elif event == "message_delta":
                        usage.update(data.get("usage", {}) or {})
        return ModelResponse(content="".join(parts), usage={k: int(v) for k, v in usage.items()
                              if isinstance(v, (int, float))})
