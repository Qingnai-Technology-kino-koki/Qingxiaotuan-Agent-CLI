"""kernel 契约层 —— 与协议无关的数据类型 + 错误模型。

契约层由青小团自研实现，统一 provider 与内置交互所需的数据形状：
- provider：ChatProvider 接口、GenerateOptions、StreamedMessage
- message：Role / ContentPart / Message / ToolCall / StreamedMessagePart
- tool：Tool
- usage：TokenUsage
- errors：ChatProviderError 层级 + 错误分类
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Union


# ===================================================================== 角色

class Role(str, enum.Enum):
    """消息角色（兼容 OpenAI / Anthropic / Kimi 三套协议的字符串值）。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

    def __str__(self) -> str:  # 便于直接拼进 dict（与 OpenAI 协议一致）
        return self.value


# ===================================================================== 内容块

@dataclass(frozen=True)
class ContentPart:
    """多模态内容块（文本 / 图片 / 音频）。"""

    type: str  # "text" | "image_url" | "input_audio"
    text: Optional[str] = None
    image_url: Optional[Dict[str, str]] = None  # {"url": "https://..." | "data:..."}
    input_audio: Optional[Dict[str, str]] = None  # {"data": "...", "format": "wav"}

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type}
        if self.text is not None:
            out["text"] = self.text
        if self.image_url is not None:
            out["image_url"] = self.image_url
        if self.input_audio is not None:
            out["input_audio"] = self.input_audio
        return out


def text_part(text: str) -> ContentPart:
    return ContentPart(type="text", text=text)


def image_url_part(url: str, detail: Optional[str] = None) -> ContentPart:
    img: Dict[str, str] = {"url": url}
    if detail:
        img["detail"] = detail
    return ContentPart(type="image_url", image_url=img)


# ===================================================================== 消息

@dataclass
class Message:
    """一条对话消息。content 可以是纯文本或内容块列表（多模态）。"""

    role: Role
    content: Union[str, List[ContentPart]] = ""
    tool_calls: List["ToolCall"] = field(default_factory=list)
    # tool 角色消息专用
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def text_content(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(p.text or "" for p in self.content if p.type == "text")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"role": str(self.role)}
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            out["name"] = self.name
        if self.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        out["content"] = (
            self.content if isinstance(self.content, str) else [p.to_dict() for p in self.content]
        )
        return out


def create_system_message(content: str) -> Message:
    return Message(role=Role.SYSTEM, content=content)


def create_user_message(content: Union[str, List[ContentPart]]) -> Message:
    return Message(role=Role.USER, content=content)


def create_assistant_message(
    content: Union[str, List[ContentPart]] = "",
    tool_calls: Optional[List["ToolCall"]] = None,
) -> Message:
    return Message(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])


def create_tool_message(tool_call_id: str, content: str, name: Optional[str] = None) -> Message:
    return Message(role=Role.TOOL, content=content, tool_call_id=tool_call_id, name=name)


# ===================================================================== 工具

@dataclass
class Tool:
    """工具声明（极简：名称 + 描述 + JSON Schema 参数）。"""

    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema
    deferred: bool = False


@dataclass
class ToolCall:
    """一次工具调用（模型发起）。arguments 为 JSON 字符串（与 OpenAI 协议一致）。"""

    id: str
    name: str
    arguments: str = ""


# ===================================================================== 用量

@dataclass
class Usage:
    """Token 用量（含缓存读/写）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    input_cache_read_tokens: int = 0
    input_cache_creation_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.input_tokens,
            "completion_tokens": self.output_tokens,
            "prompt_cache_read_tokens": self.input_cache_read_tokens,
            "prompt_cache_creation_tokens": self.input_cache_creation_tokens,
        }


# ===================================================================== 结束原因

class FinishReason(str, enum.Enum):
    """生成结束原因。"""

    COMPLETED = "completed"
    TOOL_CALLS = "tool_calls"
    TRUNCATED = "truncated"
    FILTERED = "filtered"
    PAUSED = "paused"
    OTHER = "other"

    def __str__(self) -> str:
        return self.value


# ===================================================================== 流式分片 + 流

@dataclass
class StreamedMessagePart:
    """流式分片。type 决定如何合并进最终 Message。

    取值：
    - "text_delta": text 增量
    - "thinking_delta": 思维链增量（reasoning）
    - "tool_call_part": 工具调用增量（id/name/arguments_delta/index）
    - "usage": usage 元数据
    - "finish": finish_reason / raw_finish_reason 元数据
    """

    type: str
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    arguments_delta: Optional[str] = None
    index: Optional[int] = None  # OpenAI 流式 tool_call 用 index 分组（id/name 仅首片带）
    usage: Optional[Usage] = None
    finish_reason: Optional[FinishReason] = None
    raw_finish_reason: Optional[str] = None


class StreamedMessage:
    """一次模型生成的流式结果。

    实现 `async for part in streamed` 拿到分片；迭代结束后可读 `id` / `usage`
    / `finish_reason` / `trace_id` 等元数据（由 provider 在迭代过程中填充）。
    流式消息对象（含异步迭代接口）。
    """

    def __init__(self, aiter: AsyncIterator[StreamedMessagePart]) -> None:
        self._aiter = aiter
        self.id: Optional[str] = None
        self.usage: Optional[Usage] = None
        self.finish_reason: Optional[FinishReason] = None
        self.raw_finish_reason: Optional[str] = None
        self.trace_id: Optional[str] = None
        self.request_id: Optional[str] = None

    # 类级默认值：provider 常通过 StreamedMessage.__new__() 构造再手动赋值，
    # 这些字段必须始终存在。
    id: Optional[str] = None
    usage: Optional["Usage"] = None
    finish_reason: Optional["FinishReason"] = None
    raw_finish_reason: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None

    def __aiter__(self) -> AsyncIterator[StreamedMessagePart]:
        return self._aiter


# ===================================================================== 选项 + 结果 + 接口

@dataclass
class GenerateOptions:
    """generate 的可选参数。"""

    signal: Optional[Any] = None  # asyncio.CancelledError / 自定义取消令牌
    auth: Optional[Dict[str, str]] = None  # 额外鉴权头（如 api_key 之外的 header）
    max_completion_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    cache_key: Optional[str] = None  # 部分厂商支持 prompt 缓存键（nvidia 不支持 → 发送前剔除）
    on_request_start: Optional[Callable[[], None]] = None
    on_request_sent: Optional[Callable[[], None]] = None
    on_stream_end: Optional[Callable[[], None]] = None
    on_trace_id: Optional[Callable[[str], None]] = None


@dataclass
class GenerateResult:
    """generate() 的最终结果。"""

    message: Message
    finish_reason: FinishReason
    raw_finish_reason: Optional[str]
    usage: Usage
    trace_id: Optional[str]
    id: Optional[str]


# ChatProvider 用 Protocol 声明（结构化子类，便于静态检查与鸭子类型）
class ChatProvider:
    """大模型 provider 统一接口。

    实现类必须提供：name / model_name / thinking_effort / max_completion_tokens 属性，
    以及 async generate(...) 方法。
    """

    name: str = "base"
    model_name: str = ""
    thinking_effort: Optional[str] = None
    max_completion_tokens: Optional[int] = None

    async def generate(
        self,
        system_prompt: str,
        tools: List[Tool],
        history: List[Message],
        options: Optional[GenerateOptions] = None,
        callbacks: Optional["GenerateCallbacks"] = None,
    ) -> StreamedMessage:
        raise NotImplementedError


@dataclass
class GenerateCallbacks:
    """generate 过程中的流式回调。"""

    on_token: Optional[Callable[[str], None]] = None
    on_reasoning: Optional[Callable[[str], None]] = None
    on_tool_call: Optional[Callable[[Message], None]] = None
    on_trace_id: Optional[Callable[[str], None]] = None


# ===================================================================== 错误模型

class ChatProviderError(Exception):
    """provider 错误的基类。"""

    code: str = "provider_error"

    def __init__(self, message: str, *, code: Optional[str] = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class APIConnectionError(ChatProviderError):
    code = "connection_error"


class APITimeoutError(APIConnectionError):
    code = "timeout"


class APIStatusError(ChatProviderError):
    """带 HTTP 状态码的错误。"""

    code = "status_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: Optional[str] = None,
        retry_after_ms: Optional[int] = None,
        trace_id: Optional[str] = None,
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(message, code=self.code)
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after_ms = retry_after_ms
        self.trace_id = trace_id
        self.body = body


class APIContextOverflowError(APIStatusError):
    code = "context_overflow"


class APIRequestTooLargeError(APIStatusError):
    code = "request_too_large"


class APIProviderRateLimitError(APIStatusError):
    code = "rate_limit"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 429)
        super().__init__(message, **kwargs)


class APIProviderQuotaExhaustedError(APIStatusError):
    code = "quota_exhausted"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 429)
        super().__init__(message, **kwargs)


class VideoUploadUnsupportedError(ChatProviderError):
    code = "video_upload_unsupported"


class APIEmptyResponseError(ChatProviderError):
    code = "empty_response"


# ----------------------------------------------------------------- 错误分类

def is_context_overflow_code(status_code: int, body: Optional[Any]) -> bool:
    if status_code == 413:
        return True
    if status_code == 400 and isinstance(body, dict):
        err = body.get("error", {})
        code = (err.get("code") or "").lower() if isinstance(err, dict) else ""
        msg = (err.get("message") or "").lower() if isinstance(err, dict) else ""
        return "context_length_exceeded" in code or "maximum context" in msg
    return False


def is_insufficient_quota_code(status_code: int, body: Optional[Any]) -> bool:
    if status_code == 429 and isinstance(body, dict):
        err = body.get("error", {})
        code = (err.get("code") or "").lower() if isinstance(err, dict) else ""
        return "insufficient_quota" in code
    return False


def parse_retry_after_ms(headers: Optional[Dict[str, str]] = None, body: Optional[Any] = None) -> Optional[int]:
    """从响应头或 body 解析重试等待毫秒数。"""
    if headers:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw:
            try:
                secs = float(raw)
                return int(secs * 1000)
            except (TypeError, ValueError):
                pass
    if isinstance(body, dict):
        err = body.get("error", {})
        if isinstance(err, dict) and err.get("retry_after_ms"):
            try:
                return int(err["retry_after_ms"])
            except (TypeError, ValueError):
                pass
    return None


def classify_api_error(
    status_code: int,
    body: Optional[Any] = None,
    *,
    message: str = "",
    request_id: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> ChatProviderError:
    """根据 HTTP 状态码 + body 把错误归一为具体 ChatProviderError 子类。"""
    retry_after = parse_retry_after_ms(headers, body)
    if status_code == 429:
        if is_insufficient_quota_code(status_code, body):
            return APIProviderQuotaExhaustedError(
                message or "配额已耗尽", status_code=429, request_id=request_id, retry_after_ms=retry_after
            )
        return APIProviderRateLimitError(
            message or "触发速率限制", status_code=429, request_id=request_id, retry_after_ms=retry_after
        )
    if is_context_overflow_code(status_code, body):
        return APIContextOverflowError(
            message or "上下文超出模型上限", status_code=status_code, request_id=request_id
        )
    if status_code == 413:
        return APIRequestTooLargeError(
            message or "请求体过大", status_code=status_code, request_id=request_id
        )
    return APIStatusError(
        message or f"模型接口返回错误 (HTTP {status_code})",
        status_code=status_code,
        request_id=request_id,
        retry_after_ms=retry_after,
        body=body,
    )
