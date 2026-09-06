"""MCP SSE 客户端 —— 通过 Server-Sent Events 与 MCP server 通信。

说明：标准 MCP SSE 传输要求 client->server 走 POST、server->client 走一条长连接
``GET`` SSE 流（且首包通常是 ``endpoint`` 事件给出回调 POST 地址）。完整实现较复杂，
本模块在 ``HttpMcpClient`` 之上做**简化变体**：

- 请求（initialize / tools/list / tools/call / ping）复用 POST + JSON-RPC（与 HTTP 一致）。
- ``connect()`` 额外后台开启一条 ``GET`` SSE 流，用于消费 server 主动推送的通知；
  流断开时触发 ``on_unexpected_close``（若服务端要求先发 ``endpoint`` 事件再 POST，
  则该简化客户端可能不兼容此类纯 SSE server，需配合支持 POST 的 Streamable HTTP 端点）。

更严谨的 SSE endpoint 发现留作 TODO（不引入 ``mcp`` SDK 前提下后续可补全）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

import httpx

from .client_http import HttpMcpClient
from .types import UnexpectedCloseReason


class SseMcpClient(HttpMcpClient):
    """SSE 传输的简化客户端：POST 发请求 + 后台 GET SSE 收通知。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sse_task: Optional[asyncio.Task] = None
        self._sse_headers: Dict[str, str] = {}

    async def connect(self) -> None:
        await super().connect()
        # 后台 SSE 收流（server->client）。以 Accept: text/event-stream 的 GET 打开。
        self._sse_headers = {"Accept": "text/event-stream"}
        self._sse_headers.update(self._headers)
        self._sse_task = asyncio.ensure_future(self._sse_listen())

    async def close(self) -> None:
        if self._sse_task is not None and not self._sse_task.done():
            self._sse_task.cancel()
        self._sse_task = None
        await super().close()

    async def _sse_listen(self) -> None:
        try:
            async with self._client.stream(
                "GET", self._url, headers=self._sse_headers
            ) as resp:
                async for _line in resp.aiter_lines():
                    # 仅消费 server 推送，不处理内容（简化）
                    continue
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # 流异常断开
            self._last_error = exc
            if not self._closed:
                listener: Optional[Callable[[UnexpectedCloseReason], None]] = self._unexpected_close_listener
                if listener is not None:
                    try:
                        listener({"error": exc, "stderr": None})
                    except Exception:
                        pass
