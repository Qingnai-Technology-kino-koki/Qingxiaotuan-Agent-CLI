"""Kimi provider —— OpenAI 兼容协议（baseProtocol: 'openai'）。

Kimi 的 API 是 OpenAI chat/completions 兼容端点，因此直接复用 OpenAILegacyChatProvider，
仅把 base_url 默认指向 moonshot，模型默认 kimi-k2。这也意味着 nvidia 的
`prompt_cache_key` 修复对“以 OpenAI 兼容方式指向 NVIDIA”的端点同样生效。
"""

from __future__ import annotations

from typing import Optional

from .openai_legacy import OpenAILegacyChatProvider


class KimiChatProvider(OpenAILegacyChatProvider):
    """Kimi (Moonshot) provider —— baseProtocol=openai，端点 api.moonshot.ai。"""

    name = "kimi"

    def __init__(
        self,
        *,
        base_url: str = "https://api.moonshot.ai/v1",
        api_key: Optional[str] = None,
        model: str = "kimi-k2",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        extra_headers: Optional[dict] = None,
        timeout: float = 120.0,
        connect_timeout: float = 10.0,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            is_nvidia=False,
            extra_headers=extra_headers,
            timeout=timeout,
            connect_timeout=connect_timeout,
        )
