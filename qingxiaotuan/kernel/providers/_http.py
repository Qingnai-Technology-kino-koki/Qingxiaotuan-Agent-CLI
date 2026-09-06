"""kernel provider 共享 HTTP 工具（httpx 直连，零额外依赖）。

含上游 0.29→0.39 仍未修复缺陷的强制修复：
- nvidia 系 provider 在请求体发出前必须剔除 `prompt_cache_key`（该字段会让 NVIDIA OpenAI 兼容
  接口直接报错）。青小团对该缺陷的 Python 侧修复。
- 空值保护：请求体为空 / 非 dict / 字段不存在时直接跳过，不改变其它 provider 的请求结构。
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import httpx

from ..contract import APIConnectionError, APIStatusError, APITimeoutError, classify_api_error

# NVIDIA 兼容端点的主机后缀（用于按 baseUrl 判定 nvidia，兼顾“OpenAI 兼容方式指向 NVIDIA”的情况）
_NVIDIA_HOST_RE = re.compile(r"(^|\.)nvidia\.(com|net|io|ai)$", re.IGNORECASE)


def is_nvidia_provider(type_: Optional[str], base_url: Optional[str]) -> bool:
    """判定 provider 是否为 nvidia（上游缺陷修复的触发条件）。

    Args:
        type_: ProviderConfig.type（如 "nvidia"）。
        base_url: provider 的 baseUrl（可能指向 NVIDIA 网关）。
    """
    if type_ == "nvidia":
        return True
    if isinstance(base_url, str) and base_url:
        try:
            from urllib.parse import urlparse

            host = urlparse(base_url).hostname or ""
            if _NVIDIA_HOST_RE.search(host):
                return True
        except Exception:  # noqa: BLE001 - 非法 URL 按非 nvidia 处理
            return False
    return False


def strip_prompt_cache_key(body: Any) -> None:
    """发出请求前剔除 `prompt_cache_key`（仅对 nvidia 调用）。

    空值保护：body 为空 / 非 dict / 字段不存在时直接跳过，绝不改动其它结构。
    """
    if body is None or not isinstance(body, dict):
        return
    if "prompt_cache_key" in body:
        # 沿用仓库既有 eslint 风格意图：显式 del（Python 无对应 lint 限制）
        del body["prompt_cache_key"]


def build_client(
    base_url: str,
    api_key: Optional[str],
    *,
    timeout: float = 120.0,
    connect_timeout: float = 10.0,
    extra_headers: Optional[Dict[str, str]] = None,
) -> httpx.AsyncClient:
    """构造一个带默认超时与鉴权头的 httpx 异步客户端。"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
    )


async def iter_sse(response: httpx.Response) -> AsyncIterator[Tuple[str, str]]:
    """逐条解析 SSE 流，yield (event, data_str)。

    OpenAI 流式只用 `data:`（event 默认 "message"）；Anthropic 用 `event:` + `data:`。
    `data: [DONE]` 不会在此 yield（由调用方按内容判断停止）。
    """
    event = "message"
    data_lines: list = []
    async for line in response.aiter_lines():
        if not line:  # 空行 = 一条事件结束
            if data_lines:
                data_str = "\n".join(data_lines)
                if data_str.strip() != "[DONE]":
                    yield event, data_str
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):  # SSE 注释
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    # 残留在缓冲区的最后一条
    if data_lines:
        data_str = "\n".join(data_lines)
        if data_str.strip() != "[DONE]":
            yield event, data_str


def raise_for_status(response: httpx.Response) -> None:
    """把非 2xx 响应归一为 ChatProviderError 子类。"""
    if response.is_success:
        return
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = response.text
    err = classify_api_error(
        response.status_code,
        body,
        message=f"模型接口错误 (HTTP {response.status_code})",
        request_id=response.headers.get("x-request-id"),
        headers=dict(response.headers),
    )
    raise err


def _wrap_http_errors(fn):
    """把 httpx 连接/超时异常归一为 ChatProviderError（供 provider 复用）。"""

    async def _wrapped(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except APIStatusError:
            raise
        except httpx.TimeoutException as exc:
            raise APITimeoutError(f"请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise APIConnectionError(f"连接错误: {exc}") from exc

    return _wrapped
