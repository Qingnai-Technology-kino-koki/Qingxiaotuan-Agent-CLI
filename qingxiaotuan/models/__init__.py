"""模型适配层 —— Harness 理念: 模型是可插拔的, Harness 不绑定任何一家。

青小团的核心卖点之一: 用户想用哪个"脑子"就用哪个。DeepSeek 不强时,
直接切到 Claude / Gemini / 本地网关 (Ollama / vLLM / LM Studio 等) 即可 ——
只要端点是 OpenAI 兼容协议 (chat/completions + tools), 一套适配器通吃。

48 家开箱即用供应商, 按分类覆盖:
  A. 中国主流 (14 家): DeepSeek, 通义千问, Kimi, GLM, 豆包, 百度文心, 讯飞星火 等
  B. 国际主流 (12 家): OpenAI, Anthropic, Gemini, Mistral, xAI, Cohere 等
  C. 聚合网关 (6 家): OpenRouter, SiliconFlow, Novita, Lepton, Cloudflare, GitHub Models
  D. 云平台 (6 家): 火山引擎, 百度千帆, 腾讯混元, 华为盘古, AWS Bedrock, Azure
  E. 免费/低门槛 (6 家): Groq, Together, Fireworks, Novita, GitHub Models, Cloudflare
  F. 自托管/本地 (4 家): Ollama, LM Studio, vLLM, 通用本地网关
"""

from typing import Dict

from .base import ModelAdapter, ModelCapabilities, ModelResponse, ToolCall
from .openai_compat import OpenAICompatAdapter
from .anthropic import AnthropicAdapter
from .provider_catalog import (
    ALL_PROVIDERS, ALL_PROVIDER_NAMES, PROVIDER_CATEGORIES,
    ProviderPreset, get_provider, search_providers, get_free_providers,
    get_cn_providers, get_global_providers,
)

__all__ = [
    "ModelAdapter", "ModelCapabilities", "ModelResponse", "ToolCall",
    "OpenAICompatAdapter", "AnthropicAdapter",
    "create_adapter", "KNOWN_PROVIDERS", "is_known_provider", "PROVIDER_PRESETS",
    "ALL_PROVIDERS", "ALL_PROVIDER_NAMES", "PROVIDER_CATEGORIES",
    "ProviderPreset", "get_provider", "search_providers",
    "get_free_providers", "get_cn_providers", "get_global_providers",
]

# 所有"已知" provider (仅用于友好提示与文档; 实际一律走 OpenAI 兼容适配器)。
# 未知 provider 不再报错, 而是按 openai-compatible 网关处理 —— 极大放开扩展性。
# 48 家开箱即用供应商 (详见 provider_catalog.py); 外加通用网关 openai-compatible。
KNOWN_PROVIDERS = tuple(list(ALL_PROVIDER_NAMES) + ["openai-compatible", "anthropic-gw"])

# 48 家开箱即用的供应商预设: 选了就自动带好 base_url / 推荐模型 / 密钥变量。
# 用户 `qxt model` 直接挑一家即可, 不用手填任何地址。
# 保持向后兼容: 旧代码中 `PROVIDER_PRESETS["deepseek"]` 仍可工作。
PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    p.name: p.to_dict() for p in ALL_PROVIDERS
}
# 补充 anthropic-gw (向后兼容, 与 anthropic 共享 base_url/api_key)
PROVIDER_PRESETS["anthropic-gw"] = {
    "base_url": "https://api.anthropic.com/v1",
    "model": "claude-3-5-sonnet-latest",
    "api_key_env": "ANTHROPIC_API_KEY",
    "desc": "Claude (走 OpenAI 兼容网关, 需中转; 原生请用 anthropic provider)",
}


def is_known_provider(provider: str) -> bool:
    """判断 provider 是否在已知清单内 (仅用于提示, 不影响可用性)。"""
    return provider in KNOWN_PROVIDERS


def get_provider_info(provider: str):
    """获取供应商详细信息 (结构化数据), 未知供应商返回 None。"""
    return get_provider(provider)


def create_adapter(config) -> ModelAdapter:
    """根据配置创建模型适配器。

    设计原则: 不绑架任何一家模型。
    - 已知 provider (deepseek / openai / moonshot ...) 与未知 provider 都走
      OpenAICompatAdapter (chat/completions + tools 协议统一)。
    - 未知 provider 不再抛 ValueError, 而是按 openai-compatible 网关处理;
      缺失 base_url 时给清晰报错, 引导用户填网关地址。
    """
    provider = config.get("model.provider", "deepseek")
    base_url = config.get("model.base_url")
    if not is_known_provider(provider):
        # 未知 provider = 用户自定义的 OpenAI 兼容网关 (Claude/Gemini/本地等)。
        # 必须给出 base_url, 否则无从连接。
        if not base_url:
            raise ValueError(
                f"未知模型 provider: {provider!r} (不在内置清单 {', '.join(KNOWN_PROVIDERS)})。\n"
                f"若它是 OpenAI 兼容网关, 请在 model.base_url 填网关地址, 例如:\n"
                f"  qxt model set {provider} <model> https://your-gateway/v1\n"
                f"或先用已知 provider: qxt model set openai-compatible <model> <base_url>"
            )
    adapter_cls = AnthropicAdapter if provider == "anthropic" else OpenAICompatAdapter
    return adapter_cls(
        base_url=base_url,
        model=config.get("model.model"),
        api_key=config.api_key(),  # 延迟校验: 真正发请求时 client 属性才检查
        temperature=config.get("model.temperature", 0.7),
        max_tokens=config.get("model.max_tokens", 8192),
        timeout=config.get("model.timeout", 120),
        connect_timeout=config.get("model.connect_timeout", 10.0),
        read_timeout=config.get("model.read_timeout", 120.0),
        prompt_cache=config.get("model.prompt_cache", True),
    )
