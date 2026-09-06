"""kernel —— 青小团自研的大模型抽象层。

设计目标：
- 一组与协议无关的「契约」数据类型（Message / Tool / ToolCall / ContentPart / Usage / StreamedMessage）。
- 一个顶层 `generate()` 流式聚合器（协议无关），负责把分片拼成完整 Message、增量拼接 tool_call。
- 多个 provider 实现（OpenAI responses / OpenAI chat-completions / Anthropic / Kimi），统一走 `ChatProvider` 接口。
- 一个轻量 `ProviderService`（DI 等价物），按 `ProviderConfig` 选择/构造 provider。

统一入口约束（来自上游 0.29→0.39 仍存在的缺陷修复）：
- nvidia 系 provider 在请求发出前必须剔除 `prompt_cache_key`（见 providers/_openai_base.py 的 strip 逻辑），
  其它 provider 的请求结构不得改变。空值保护：请求体为空/非对象/字段不存在时直接跳过。

本包用 httpx 直连各厂商 REST（不引入官方 SDK），以零新依赖覆盖 48 家 OpenAI 兼容供应商 + Anthropic + Google。
"""

from .contract import (
    ChatProvider,
    ChatProviderError,
    ContentPart,
    FinishReason,
    GenerateOptions,
    GenerateResult,
    Message,
    Role,
    StreamedMessage,
    StreamedMessagePart,
    Tool,
    ToolCall,
    Usage,
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    APIContextOverflowError,
    APIRequestTooLargeError,
    APIProviderRateLimitError,
    APIProviderQuotaExhaustedError,
    VideoUploadUnsupportedError,
    APIEmptyResponseError,
    classify_api_error,
    parse_retry_after_ms,
    text_part,
    image_url_part,
    create_user_message,
    create_assistant_message,
    create_tool_message,
    create_system_message,
)
from .generate import generate
from .provider_service import ProviderConfig, ProviderService, build_provider

__all__ = [
    "ChatProvider",
    "ChatProviderError",
    "ContentPart",
    "FinishReason",
    "GenerateOptions",
    "GenerateResult",
    "Message",
    "Role",
    "StreamedMessage",
    "StreamedMessagePart",
    "Tool",
    "ToolCall",
    "Usage",
    "APIConnectionError",
    "APITimeoutError",
    "APIStatusError",
    "APIContextOverflowError",
    "APIRequestTooLargeError",
    "APIProviderRateLimitError",
    "APIProviderQuotaExhaustedError",
    "VideoUploadUnsupportedError",
    "APIEmptyResponseError",
    "classify_api_error",
    "parse_retry_after_ms",
    "text_part",
    "image_url_part",
    "create_user_message",
    "create_assistant_message",
    "create_tool_message",
    "create_system_message",
    "generate",
    "ProviderConfig",
    "ProviderService",
    "build_provider",
]
