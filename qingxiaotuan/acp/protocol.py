"""ACP 协议原语: JSON-RPC 帧化 + 方法/通知名常量 + 结果构造器。

所有消息均为 newline-delimited JSON (NDJSON), 一行一条, 与 opencode/Zed ACP 约定一致。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Iterator, List, Optional

# ---- JSON-RPC 方法 (client -> server) ----
ACP_INITIALIZE = "initialize"
ACP_PROMPT = "prompt"
ACP_UPDATE = "update"
ACP_CANCEL = "cancel"
ACP_SHUTDOWN = "shutdown"

# ---- 通知 (双向均可发, 这里 server -> client 为主) ----
NOTIF_SESSION_UPDATE = "session/update"
NOTIF_TASK_UPDATE = "task/update"


def build_request(method: str, params: Optional[Dict[str, Any]] = None, req_id: Any = 1) -> Dict[str, Any]:
    """构造一条 JSON-RPC 请求 (server 内部用于测试/回显, 主要用 emit_*)。"""
    msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params is not None:
        msg["params"] = params
    return msg


def build_response(req_id: Any, result: Optional[Dict[str, Any]] = None,
                   error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result if result is not None else {}
    return msg


def build_notification(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def build_initialize_result(
    session_id: str,
    agent_info: Dict[str, str],
    model: str,
    model_info: Dict[str, str],
    tools: Iterable[Dict[str, Any]],
    slash_commands: Iterable[Dict[str, str]],
    workspace_folder: str,
    auth_methods: Optional[Iterable[Dict[str, Any]]] = None,
    protocol_version: Optional[int] = None,
) -> Dict[str, Any]:
    """`initialize` 的回执体 (对齐 kimi/opencode ACP 字段)。

    ``protocol_version``（融合层新增）：开启 ``fusion.acp_enhanced`` 时由
    ``negotiate_version`` 得出并回传，便于遵循 ACP 约定的 IDE 校验兼容性。
    """
    result = {
        "sessionId": session_id,
        "session": {"sessionId": session_id},
        "agentInfo": agent_info,
        "model": model,
        "modelInfo": model_info,
        "tools": list(tools),
        "slash_commands": list(slash_commands),
        "workspaceFolder": {"uri": f"file://{workspace_folder}", "name": workspace_folder},
        "authMethods": list(auth_methods or []),
    }
    if protocol_version is not None:
        result["protocolVersion"] = protocol_version
    return result


def emit(writer, msg: Dict[str, Any]) -> None:
    """线程安全地写出一条 NDJSON 消息 (writer: 带 .write/.flush 的对象)。"""
    line = json.dumps(msg, ensure_ascii=False)
    writer.write(line + "\n")
    writer.flush()


def read_messages(reader) -> Iterator[Dict[str, Any]]:
    """从 reader (支持 .readline()) 逐行解析 NDJSON-RPC 消息。"""
    for raw in reader:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            # 非法帧: 忽略, 不中断 server 循环
            continue


# ---------------------------------------------------------------------------
# 融合层: 增量字节帧缓冲 + 错误容忍解析（复用自 kernel ``acp/codec.py``）
# ---------------------------------------------------------------------------


class LineBuffer:
    """按 ``\\n`` (0x0a) 切分字节流的增量缓冲器。

    原生 ``read_messages`` 直接逐行迭代，依赖底层 reader 已给出完整行；对于原始
    字节流（socket 等），一行可能被拆成多次读取。``LineBuffer`` 把零散字节拼成
    完整行，未完成的尾巴留到下次 ``feed``。``flush()`` 在 EOF 时吐出残留尾巴。
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


def parse_frame(line: str) -> Dict[str, Any]:
    """解析单行 JSON-RPC 文本为归一化字典。

    返回固定结构 ``{"jsonrpc","id","method","params","result","error"}``，缺失填
    ``None``。非法 JSON 或非法类型返回带 ``error`` 的归一化结构（不抛异常），
    让上层按解析失败处理，避免中断 stdio 主循环。
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return {
            "jsonrpc": None, "id": None, "method": None,
            "params": None, "result": None,
            "error": {"code": -32700, "message": "Parse error"},
        }
    if not isinstance(obj, dict):
        return {
            "jsonrpc": None, "id": None, "method": None,
            "params": None, "result": None,
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


def read_messages_robust(reader) -> Iterator[Dict[str, Any]]:
    """错误容忍的 NDJSON-RPC 消息迭代器（融合层）。

    与 ``read_messages`` 行为兼容：逐行解析、跳过空行与非法帧。区别是用
    ``parse_frame`` 归一化每帧（非法帧被跳过而非抛错），并兼容直接喂字节的 reader
    （通过 ``LineBuffer`` 拼行）。供 ``fusion.acp_enhanced`` 开启时使用。
    """
    # 若 reader 提供二进制读取 (readinto/read)，按字节走 LineBuffer；否则按行迭代。
    if hasattr(reader, "read") and not hasattr(reader, "__iter__"):
        buf = LineBuffer()
        while True:
            chunk = reader.read(4096)
            if not chunk:
                for line in buf.flush():
                    text = line.decode("utf-8", "replace").strip()
                    if text:
                        parsed = parse_frame(text)
                        if parsed.get("error") is None:
                            yield parsed
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            for line in buf.feed(chunk):
                text = line.decode("utf-8", "replace").strip()
                if text:
                    parsed = parse_frame(text)
                    if parsed.get("error") is None:
                        yield parsed
        return
    for raw in reader:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        raw = raw.strip()
        if not raw:
            continue
        parsed = parse_frame(raw)
        if parsed.get("error") is None:
            yield parsed
