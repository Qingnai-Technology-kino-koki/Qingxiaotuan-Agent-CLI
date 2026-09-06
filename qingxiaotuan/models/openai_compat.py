"""OpenAI 兼容协议适配器 —— 一套代码适配 DeepSeek / OpenAI / OpenRouter / Moonshot / OpenCode Zen 等。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from .base import ModelAdapter, ModelCapabilities, ModelResponse, ToolCall

if TYPE_CHECKING:
    from openai import OpenAI


class OpenAICompatAdapter(ModelAdapter):
    name = "openai-compat"
    capabilities = ModelCapabilities(
        tool_calling=True, function_calling=True, streaming=True,
        vision=True, json_mode=True,
    )

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str],
        temperature: float = 0.7,
        max_tokens: int = 8192,
        timeout: float = 120,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
        prompt_cache: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.prompt_cache = prompt_cache
        self._client: Optional[OpenAI] = None
        self._api_key = api_key

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not self._api_key:
                # 本地/自托管端点 (Ollama / llama.cpp / LM Studio / vLLM 等 OpenAI 兼容服务)
                # 通常不需要真实密钥, 缺失时回落一个占位 key, 避免本地测试被卡在鉴权。
                # 远程端点仍严格要求真实密钥 (下方 import openai 后由服务端返回 401)。
                if "localhost" in self.base_url or "127.0.0.1" in self.base_url:
                    self._api_key = "sk-local-llm"
                else:
                    raise RuntimeError(
                        "未配置 API Key。请运行 `qxt setup`, 或设置环境变量 "
                        "DEEPSEEK_API_KEY / OPENCODE_ZEN_API_KEY / QXT_API_KEY。"
                    )
            # 延迟导入: openai SDK 很重 (pydantic 类型), 只在真正发请求时加载。
            # 关键: 它绝不是核心依赖 —— 不使用 OpenAI 兼容传输层时 (本地/Ollama/Anthropic
            # 等) 完全不需要安装 openai, 导入本模块也不会触碰它 (见文件顶部 TYPE_CHECKING)。
            import httpx
            try:
                from openai import OpenAI
            except ImportError as exc:  # 友好报错: 仅 OpenAI 传输层需要该 SDK
                raise RuntimeError(
                    "未安装 openai SDK, 但当前 Provider 需要 OpenAI 兼容传输层。\n"
                    "该依赖仅作 HTTP 传输层使用, 核心逻辑不依赖它; 运行非 OpenAI 场景无需安装。\n"
                    "安装方式: pip install qingxiaotuan[openai]  或  pip install openai>=1.40"
                ) from exc
            # 分离连接与读超时; 流式场景读超时作为分片间最大空闲
            timeout_cfg: Any
            if self.connect_timeout and self.read_timeout:
                timeout_cfg = httpx.Timeout(
                    connect=self.connect_timeout,
                    read=self.read_timeout,
                    write=self.connect_timeout,
                    pool=self.connect_timeout,
                )
            else:
                timeout_cfg = self.timeout
            self._client = OpenAI(
                base_url=self.base_url,
                api_key=self._api_key,
                timeout=timeout_cfg,
                max_retries=0,  # 重试用 agent 层统一控制, 避免 SDK 与业务逻辑双重重试
            )
        return self._client

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        on_reason: Optional[Callable[[str], None]] = None,
    ) -> ModelResponse:
        # Prompt 缓存: 把稳定的 system 前缀打上 cache_control, 命中后只计费增量。
        # 兼容 DeepSeek / OpenCode Zen 等支持 message 级缓存的 OpenAI 兼容端点。
        if self.prompt_cache:
            messages = self._mark_cache(messages, tools)
            # 工具定义稳定, 一并打缓存标记 (复制, 不污染调用方)
            tools = self._mark_tools_cache(tools)
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if stream:
            return self._chat_stream(kwargs, on_token, on_reason)
        resp = self.client.chat.completions.create(**kwargs)
        return self._parse(resp.choices[0], getattr(resp, "usage", None))

    @staticmethod
    def _mark_cache(
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """给稳定的 system 消息打 cache_control 标记 (prompt 缓存断点)。

        只标记最后一条 system 消息为缓存断点; 工具定义紧跟其后同样稳定,
        一并标记。复制对象, 不污染调用方的原始 messages / tools。
        返回打过标记的 messages (tools 的标记在 chat() 内单独处理)。
        """
        out = [dict(m) for m in messages]
        last_sys = None
        for i in range(len(out) - 1, -1, -1):
            if out[i].get("role") == "system":
                last_sys = i
                break
        if last_sys is not None:
            out[last_sys]["cache_control"] = {"type": "ephemeral"}
        return out

    @staticmethod
    def _mark_tools_cache(
        tools: Optional[List[Dict[str, Any]]],
    ) -> Optional[List[Dict[str, Any]]]:
        """给稳定的工具定义打 cache_control 标记 (复制, 不污染原列表)。

        部分 OpenAI 兼容端点 (DeepSeek / OpenCode Zen) 支持在 tools 上缓存,
        与 system 前缀一并命中, 进一步抬高缓存命中率。
        """
        if not tools:
            return None
        marked = []
        for t in tools:
            t2 = dict(t)
            fn = dict(t2.get("function", {}))
            fn["cache_control"] = {"type": "ephemeral"}
            t2["function"] = fn
            marked.append(t2)
        return marked

    # ------------------------------------------------------------------ 内部

    def _chat_stream(self, kwargs: Dict[str, Any], on_token, on_reason) -> ModelResponse:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_acc: Dict[int, Dict[str, str]] = {}
        finish_reason = "stop"
        usage: Dict[str, int] = {}

        for chunk in self.client.chat.completions.create(**kwargs):
            if getattr(chunk, "usage", None):
                usage = _extract_usage(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
            if getattr(delta, "reasoning_content", None):
                reasoning_parts.append(delta.reasoning_content)
                if on_reason:
                    on_reason(delta.reasoning_content)
            if delta.content:
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    slot = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] += tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

        return ModelResponse(
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=[ToolCall(**tool_acc[i]) for i in sorted(tool_acc)],
            finish_reason=finish_reason,
            usage=usage,
        )

    # ------------------------------------------------------------ 错误分类

    @staticmethod
    def classify_error(exc: Exception) -> Tuple[str, Optional[int]]:
        """把异常归类为 ('rate_limit'|'timeout'|'server'|'auth'|'other', status_code)。"""
        import httpx
        # openai 库异常
        status = getattr(exc, "status_code", None)
        if status is None:
            # httpx 异常可能包在 openai 异常里
            cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
            status = getattr(cause, "status_code", None)
        msg = str(exc).lower()
        if status == 429 or "rate" in msg or "too many requests" in msg:
            return "rate_limit", status or 429
        if status in (401, 403) or "auth" in msg or "unauthorized" in msg or "invalid api key" in msg:
            return "auth", status or 401
        if status in (500, 502, 503, 504) or "server error" in msg:
            return "server", status
        if isinstance(exc, (httpx.TimeoutException,)) or "timeout" in msg or status in (408, 409):
            return "timeout", status or 408
        return "other", status

    @staticmethod
    def _parse(choice, usage) -> ModelResponse:
        msg = choice.message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "")
            for tc in (msg.tool_calls or [])
        ]
        return ModelResponse(
            content=msg.content or "",
            reasoning=getattr(msg, "reasoning_content", "") or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=_extract_usage(usage),
        )


def _extract_usage(usage) -> Dict[str, int]:
    """统一抽取 token 用量, 含 DeepSeek 的 prompt cache 命中字段。

    DeepSeek / OpenCode Zen 在 usage 里返回:
        prompt_tokens, completion_tokens,
        prompt_cache_hit_tokens, prompt_cache_miss_tokens
        (部分版本还有 cache_write_token)。
    我们把它们全部归一化进 dict, 供 UI 计算命中率。
    """
    if not usage:
        return {}
    src = usage if isinstance(usage, dict) else usage
    out: Dict[str, int] = {}
    for key in (
        "prompt_tokens", "completion_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
        "cache_write_token", "total_tokens",
    ):
        val = getattr(src, key, None)
        if val is None and isinstance(src, dict):
            val = src.get(key)
        if isinstance(val, (int, float)):
            out[key] = int(val)
    return out
