"""ACP NDJSON 帧编解码 (JSON-RPC 2.0)。

自研实现 —— NDJSON 帧编解码：
- 字节流按 ``\\n`` (0x0a) 切分，每行一条 JSON-RPC 2.0 消息。
- stdout 是协议通道，因此任何日志/console 都必须重定向到 stderr（见 ``run_acp_server_stdio``）。
- 提供 request / response / notification / error 四类帧的构造与解析。

本模块零依赖（仅 ``json`` / ``typing``），被 server.py 用于 stdio 主循环，
也被 tests 直接调用做帧往返断言。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

JSONRPC = "2.0"
CANCEL_REQUEST_NOTIFICATION = "$/cancel_request"


class LineBuffer:
    """按 ``\\n`` (0x0a) 切分字节流的增量缓冲器。

    ``feed(chunk)`` 返回本批已完成的若干行（含末尾之前的所有完整行，不含
    换行符本身）；未完成的尾巴留在内部，等下一次 ``feed`` 续上。``flush()``
    把残留尾巴作为最后一行吐出（EOF 时调用）。
    """

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> List[bytes]:
        self._buf += chunk
        lines: List[bytes] = []
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            lines.append(self._buf[:idx])
            self._buf = self._buf[idx + 1:]
        return lines

    def flush(self) -> List[bytes]:
        if self._buf:
            out = [self._buf]
            self._buf = b""
            return out
        return []

    def reset(self) -> None:
        self._buf = b""


def dumps(frame: Dict[str, Any]) -> str:
    """把一个 JSON-RPC 帧序列化成单行字符串。"""
    return json.dumps(frame, ensure_ascii=False)


def encode_request(id: Any, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造一条 JSON-RPC 请求 (client -> server 或 server -> client 反向 RPC)。"""
    msg: Dict[str, Any] = {"jsonrpc": JSONRPC, "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def encode_response(id: Any, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造一条 JSON-RPC 成功响应。"""
    return {
        "jsonrpc": JSONRPC,
        "id": id,
        "result": result if result is not None else {},
    }


def encode_error(id: Any, code: int, message: str, data: Optional[Any] = None) -> Dict[str, Any]:
    """构造一条 JSON-RPC 错误响应。"""
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC, "id": id, "error": error}


def encode_notification(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造一条 JSON-RPC 通知 (无 ``id``，接收方不回响应)。"""
    msg: Dict[str, Any] = {"jsonrpc": JSONRPC, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def encode_cancel_request(request_id: Any) -> Dict[str, Any]:
    """构造 ``$/cancel_request`` 通知 (携带待取消请求的 id)。"""
    return encode_notification(CANCEL_REQUEST_NOTIFICATION, {"requestId": request_id})


def parse_frame(line: str) -> Dict[str, Any]:
    """解析单行 JSON-RPC 文本为归一化字典。

    返回固定结构的 dict，缺失字段填 ``None``，便于测试与路由断言：
    ``{"jsonrpc", "id", "method", "params", "result", "error"}``。

    非法 JSON 时返回 ``{"jsonrpc": None, "error": {"code": -32700, ...}}`` 形态，
    让上层按解析失败处理（不抛异常，避免中断 stdio 主循环）。
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return {
            "jsonrpc": None,
            "id": None,
            "method": None,
            "params": None,
            "result": None,
            "error": {"code": -32700, "message": "Parse error"},
        }
    if not isinstance(obj, dict):
        return {
            "jsonrpc": None,
            "id": None,
            "method": None,
            "params": None,
            "result": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    return {
        "jsonrpc": obj.get("jsonrpc"),
        "id": obj.get("id"),
        "method": obj.get("method"),
        "params": obj.get("params"),
        "result": obj.get("result"),
        "error": obj.get("error"),
    }


def is_request(frame: Dict[str, Any]) -> bool:
    """是否为一条请求 (有 method 且有 id，且非响应)。"""
    if frame.get("method") is None:
        return False
    if frame.get("result") is not None or frame.get("error") is not None:
        return False
    return frame.get("id") is not None


def is_notification(frame: Dict[str, Any]) -> bool:
    """是否为一条通知 (有 method 且无 id)。"""
    return frame.get("method") is not None and frame.get("id") is None


def is_response(frame: Dict[str, Any]) -> bool:
    """是否为一条响应 (有 id 且带 result 或 error，无 method)。"""
    return frame.get("method") is None and frame.get("id") is not None and (
        frame.get("result") is not None or frame.get("error") is not None
    )
