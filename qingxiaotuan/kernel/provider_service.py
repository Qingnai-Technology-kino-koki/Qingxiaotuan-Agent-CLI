"""kernel ProviderService —— 轻量 provider 注册中心（按配置选协议与厂商）。

把「协议协议 + 厂商差异」简化为：给定 ProviderConfig(type, base_url, api_key, model, protocol)，
选择并构造一个 ChatProvider 实例。nvidia 判定复用 _http.is_nvidia_provider。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .contract import ChatProvider
from .providers import (
    AnthropicChatProvider,
    KimiChatProvider,
    OpenAILegacyChatProvider,
    OpenAIResponsesChatProvider,
)
from .providers._http import is_nvidia_provider

# 各内置 provider 的默认 base_url（未知 openai-compatible 必须显式给 base_url）
_DEFAULT_BASE_URLS: Dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.ai/v1",
    "kimi": "https://api.moonshot.ai/v1",
}


@dataclass
class ProviderConfig:
    """一个 provider 的配置。"""

    type: str  # 'openai' | 'anthropic' | 'kimi' | 'nvidia' | 'openai-compatible' | ...
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    protocol: Optional[str] = None  # 'openai' | 'openai_responses' | 'anthropic'
    max_tokens: int = 8192
    temperature: float = 0.7
    extra_headers: Optional[Dict[str, str]] = None

    def resolve_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


def _resolve_protocol(cfg: ProviderConfig) -> str:
    """按 type / 显式 protocol 推断底层协议。"""
    if cfg.protocol:
        return cfg.protocol
    if cfg.type == "anthropic":
        return "anthropic"
    # 默认 OpenAI chat/completions（覆盖 openai / kimi / nvidia / deepseek / openai-compatible 等）
    return "openai"


def build_provider(cfg: ProviderConfig) -> ChatProvider:
    """按配置构造一个 ChatProvider 实例（统一入口）。"""
    base_url = cfg.base_url or _DEFAULT_BASE_URLS.get(cfg.type)
    api_key = cfg.resolve_api_key()
    if cfg.type == "anthropic" or _resolve_protocol(cfg) == "anthropic":
        return AnthropicChatProvider(
            base_url=base_url or "https://api.anthropic.com/v1",
            api_key=api_key,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            extra_headers=cfg.extra_headers,
        )
    if not base_url:
        raise ValueError(
            f"provider {cfg.type!r} 缺少 base_url（未知 OpenAI 兼容网关需显式配置 model.base_url）"
        )
    is_nv = is_nvidia_provider(cfg.type, base_url)
    if _resolve_protocol(cfg) == "openai_responses":
        return OpenAIResponsesChatProvider(
            base_url=base_url, api_key=api_key, model=cfg.model,
            max_tokens=cfg.max_tokens, temperature=cfg.temperature,
            is_nvidia=is_nv, extra_headers=cfg.extra_headers,
        )
    # 默认 OpenAILegacyChatProvider（Kimi 也走这条）
    if cfg.type == "kimi":
        return KimiChatProvider(
            base_url=base_url, api_key=api_key, model=cfg.model,
            max_tokens=cfg.max_tokens, temperature=cfg.temperature,
            extra_headers=cfg.extra_headers,
        )
    return OpenAILegacyChatProvider(
        base_url=base_url, api_key=api_key, model=cfg.model,
        max_tokens=cfg.max_tokens, temperature=cfg.temperature,
        is_nvidia=is_nv, extra_headers=cfg.extra_headers,
    )


class ProviderService:
    """provider 配置注册表）。

    维护一组 ProviderConfig，支持 CRUD 与变更订阅；build() 按当前激活配置构造 ChatProvider。
    """

    def __init__(self, active: Optional[ProviderConfig] = None) -> None:
        self._providers: Dict[str, ProviderConfig] = {}
        self._active_id: Optional[str] = None
        self._listeners: List[Callable[[], None]] = []
        if active is not None:
            self.register("active", active)
            self._active_id = "active"

    def register(self, provider_id: str, cfg: ProviderConfig) -> None:
        self._providers[provider_id] = cfg
        self._active_id = self._active_id or provider_id
        self._notify()

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)
        if self._active_id == provider_id:
            self._active_id = next(iter(self._providers), None)
        self._notify()

    def set_active(self, provider_id: str) -> None:
        if provider_id not in self._providers:
            raise KeyError(f"未知 provider: {provider_id}")
        self._active_id = provider_id
        self._notify()

    def get(self, provider_id: Optional[str] = None) -> Optional[ProviderConfig]:
        pid = provider_id or self._active_id
        return self._providers.get(pid) if pid else None

    def list(self) -> List[str]:
        return list(self._providers.keys())

    def on_change(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in self._listeners:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def build(self, provider_id: Optional[str] = None) -> ChatProvider:
        """构造当前激活（或指定）provider 的 ChatProvider 实例。"""
        cfg = self.get(provider_id)
        if cfg is None:
            raise RuntimeError("没有可用的 provider 配置")
        return build_provider(cfg)
