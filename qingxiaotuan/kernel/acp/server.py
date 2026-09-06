"""ACP server：把注入的 ``agent_provider`` 暴露为 IDE 可驱动的 Agent Client Protocol 服务。

自研实现 —— ACP server 主入口（NDJSON-JSON-RPC over stdio）。

关键差异 / 简化：
- 单进程可服务多 session（client 通过 ``session/new`` 显式建 session），但同一时刻只
  串行处理一个请求（stdio 主循环逐帧 await，天然串行，故 prompt 不会并发重叠）。
- 所有 I/O = newline-delimited JSON-RPC over 任意字节/文本流；stdout 是协议通道，
  console/日志必须重定向 stderr（见 ``run_acp_server_stdio``）。
- server 自身实现 :class:`~qingxiaotuan.kernel.acp.protocol.AcpConn`：既是入站帧的
  路由方，也是出站 ``session/update`` 通知与 ``session/request_permission`` 反向 RPC
  的发送方（``request_permission`` 阻塞等待客户端回响应）。
- elicitation / fs / terminal 反向 RPC 在精简版中仅保留 ``request_permission`` 入口，
  其余标 TODO（见 ``AcpServer`` 注释），不影响主链路。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from . import version as _version
from .codec import (
    CANCEL_REQUEST_NOTIFICATION,
    encode_error,
    encode_notification,
    encode_request,
    encode_response,
    is_notification,
    is_request,
    parse_frame,
)
from .protocol import (
    AcpConn,
    AUTHENTICATE,
    INITIALIZE,
    LOGOUT,
    SESSION_CANCEL,
    SESSION_CLOSE,
    SESSION_DELETE,
    SESSION_FORK,
    SESSION_LIST,
    SESSION_LOAD,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_RESUME,
    SESSION_REQUEST_PERMISSION,
    SESSION_SET_MODE,
    SESSION_SET_MODEL,
    SESSION_UPDATE,
)
from .session import AcpSession

# JSON-RPC 标准错误码
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32000
INVALID_PARAMS = -32602


class AcpServer(AcpConn):
    """Agent Client Protocol server（NDJSON-JSON-RPC over 任意流）。

    ``agent_provider`` 注入（DIP）：server 不 new agent。需提供
    ``prompt(session_id, input_text, signal) -> AsyncIterator[dict]`` 与
    ``cancel(session_id, turn_id)``；可选 ``new_session/load_session/resume_session/
    list_sessions/close_session/delete_session/set_mode/set_model/available_commands``。
    """

    def __init__(
        self,
        agent_provider: Any,
        *,
        agent_info: Optional[Dict[str, str]] = None,
        auth_methods: Optional[List[Dict[str, Any]]] = None,
        workspace: str = ".",
    ) -> None:
        self._agent_provider = agent_provider
        self._agent_info = agent_info or {"name": "qingxiaotuan", "version": "0.0.0"}
        self._auth_methods = auth_methods
        self._workspace = workspace

        self._sessions: Dict[str, AcpSession] = {}
        self._req_id = 0
        self._pending: Dict[Any, "asyncio.Future[Dict[str, Any]]"] = {}
        self._writer: Any = None

    # ============================================================ AcpConn 实现
    async def session_update(self, notification: Dict[str, Any]) -> None:
        """发送一条 ``session/update`` 通知。"""
        await self._write(encode_notification(SESSION_UPDATE, notification))

    async def request_permission(
        self, *, session_id: str, options: List[Dict[str, Any]], tool_call: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发起 ``session/request_permission`` 反向 RPC，阻塞等客户端响应。

        RPC 失败（异常或客户端回 error）时由调用方（interaction_bridge）兜底为 rejected。
        """
        rid = self._next_req_id()
        loop = asyncio.get_event_loop()
        fut: "asyncio.Future[Dict[str, Any]]" = loop.create_future()
        self._pending[rid] = fut
        await self._write(
            encode_request(
                rid, SESSION_REQUEST_PERMISSION,
                {"sessionId": session_id, "options": list(options), "toolCall": tool_call},
            )
        )
        try:
            resp = await fut
        finally:
            self._pending.pop(rid, None)
        if resp.get("error") is not None:
            raise RuntimeError(f"request_permission error: {resp['error']}")
        return resp.get("result", {}) or {}

    # ============================================================ 主循环
    async def serve(self, reader: Any, writer: Any) -> None:
        """逐帧读 NDJSON、路由 request/notification/response。"""
        self._writer = writer
        try:
            async for raw in _iter_lines(reader):
                line = raw.strip() if isinstance(raw, str) else raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                frame = parse_frame(line)
                if frame.get("jsonrpc") is None and frame.get("error") is not None:
                    # 解析失败帧
                    continue
                # 客户端回给我们的响应（针对 server 发起的反向 RPC）
                if (
                    frame.get("method") is None
                    and frame.get("id") is not None
                    and (frame.get("result") is not None or frame.get("error") is not None)
                ):
                    fut = self._pending.pop(frame["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(frame)
                    continue
                if is_request(frame):
                    await self._handle_request(frame)
                elif is_notification(frame):
                    self._handle_notification(frame)
        finally:
            self._writer = None

    async def serve_stdio(self) -> None:
        """通过真正的 stdin/stdout 运行（asyncio 管道 + console 重定向 stderr）。"""
        _redirect_console_to_stderr()
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        w_transport, w_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(w_transport, w_protocol, None, loop)
        await self.serve(reader, writer)

    # ============================================================ 请求路由
    async def _handle_request(self, frame: Dict[str, Any]) -> None:
        method = frame["method"]
        req_id = frame["id"]
        params = frame.get("params") or {}
        handler = self._request_handlers.get(method)
        if handler is None:
            await self._write(encode_error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}"))
            return
        try:
            result = await handler(params)
        except Exception as exc:  # noqa: BLE001
            await self._write(encode_error(req_id, INTERNAL_ERROR, str(exc)))
            return
        await self._write(encode_response(req_id, result if result is not None else {}))

    def _handle_notification(self, frame: Dict[str, Any]) -> None:
        method = frame["method"]
        params = frame.get("params") or {}
        handler = self._notification_handlers.get(method)
        if handler is None:
            return
        try:
            handler(params)
        except Exception:
            # 通知不回响应，失败静默
            pass

    # ============================================================ method handlers
    async def _h_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        negotiated = _version.negotiate_version(params.get("protocolVersion", 1))
        agent_capabilities = {
            "loadSession": True,
            "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
            "sessionCapabilities": {
                "list": {}, "resume": {}, "close": {}, "delete": {}, "fork": {},
            },
            "mcpCapabilities": {"http": True, "sse": True},
            "auth": {"logout": {}},
        }
        return {
            "protocolVersion": negotiated,
            "agentCapabilities": agent_capabilities,
            "authMethods": self._auth_methods or [
                {"type": "terminal", "command": ["qxt", "setup"]}
            ],
            "agentInfo": self._agent_info,
        }

    async def _h_session_new(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = await self._call_new_session(params)
        acp = AcpSession(self, session_id, self._agent_provider)
        self._sessions[session_id] = acp
        self._schedule_available_commands(acp)
        return {"sessionId": session_id, "configOptions": [], "modes": []}

    async def _h_session_load(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # 精简版：load/resume 与 new 同构（真正重载历史由 agent_provider 负责）。
        session_id = params.get("sessionId") or str(uuid.uuid4().hex)
        acp = self._sessions.get(session_id) or AcpSession(self, session_id, self._agent_provider)
        self._sessions[session_id] = acp
        self._schedule_available_commands(acp)
        return {"sessionId": session_id, "configOptions": [], "modes": []}

    async def _h_session_resume(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._h_session_load(params)

    async def _h_session_fork(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # TODO: 接 agent_provider.fork_session（引擎 fork）；精简版降级为 new。
        return await self._h_session_new({"cwd": self._workspace})

    async def _h_session_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        fn = getattr(self._agent_provider, "list_sessions", None)
        if callable(fn):
            try:
                sessions = list(fn())
                return {"sessions": [{"sessionId": s} for s in sessions]}
            except Exception:
                pass
        return {"sessions": [{"sessionId": s} for s in self._sessions.keys()]}

    async def _h_session_close(self, params: Dict[str, Any]) -> Dict[str, Any]:
        sid = params.get("sessionId")
        await self._call_void("close_session", sid)
        self._sessions.pop(sid, None)
        return {}

    async def _h_session_delete(self, params: Dict[str, Any]) -> Dict[str, Any]:
        sid = params.get("sessionId")
        await self._call_void("delete_session", sid)
        self._sessions.pop(sid, None)
        return {}

    async def _h_session_set_mode(self, params: Dict[str, Any]) -> Dict[str, Any]:
        sid = params.get("sessionId")
        mode = params.get("modeId") or params.get("mode")
        await self._call_void("set_mode", sid, mode)
        return {}

    async def _h_session_set_model(self, params: Dict[str, Any]) -> Dict[str, Any]:
        sid = params.get("sessionId")
        model_id = params.get("modelId")
        await self._call_void("set_model", sid, model_id)
        return {}

    async def _h_session_prompt(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = params.get("sessionId")
        if not session_id:
            raise ValueError("session/prompt requires a sessionId")
        acp = self._sessions.get(session_id) or self._get_or_create_session(session_id)
        blocks = params.get("prompt") or []
        return await acp.prompt(blocks)

    async def _h_authenticate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # TODO: 终端鉴权握手（device-code / terminal-auth）；精简版直接放行。
        return {}

    async def _h_logout(self, params: Dict[str, Any]) -> Dict[str, Any]:
        fn = getattr(self._agent_provider, "logout", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
        return {}

    # ============================================================ notification handlers
    def _h_session_cancel(self, params: Dict[str, Any]) -> None:
        sid = params.get("sessionId")
        acp = self._sessions.get(sid)
        if acp is not None:
            acp.cancel()

    def _h_cancel_request(self, params: Dict[str, Any]) -> None:
        rid = params.get("requestId")
        fut = self._pending.pop(rid, None) if rid is not None else None
        if fut is not None and not fut.done():
            fut.set_result({
                "jsonrpc": "2.0", "id": rid,
                "error": {"code": -32800, "message": "cancelled"},
            })

    # ============================================================ 内部工具
    @property
    def _request_handlers(self) -> Dict[str, Callable[[Dict[str, Any]], Any]]:
        return {
            INITIALIZE: self._h_initialize,
            SESSION_NEW: self._h_session_new,
            SESSION_LOAD: self._h_session_load,
            SESSION_RESUME: self._h_session_resume,
            SESSION_FORK: self._h_session_fork,
            SESSION_LIST: self._h_session_list,
            SESSION_CLOSE: self._h_session_close,
            SESSION_DELETE: self._h_session_delete,
            SESSION_SET_MODE: self._h_session_set_mode,
            SESSION_SET_MODEL: self._h_session_set_model,
            SESSION_PROMPT: self._h_session_prompt,
            AUTHENTICATE: self._h_authenticate,
            LOGOUT: self._h_logout,
        }

    @property
    def _notification_handlers(self) -> Dict[str, Callable[[Dict[str, Any]], None]]:
        return {
            SESSION_CANCEL: self._h_session_cancel,
            CANCEL_REQUEST_NOTIFICATION: self._h_cancel_request,
        }

    def _get_or_create_session(self, session_id: str) -> AcpSession:
        acp = self._sessions.get(session_id)
        if acp is None:
            acp = AcpSession(self, session_id, self._agent_provider)
            self._sessions[session_id] = acp
        return acp

    def _schedule_available_commands(self, acp: AcpSession) -> None:
        """响应 settle 后用 asyncio 延迟一拍再推 available_commands_update。

        避免客户端在注册 session 之前丢弃早期通知（Zed 行为；青小团侧延迟一拍
        广播 available_commands_update）。
        """
        loop = asyncio.get_event_loop()
        loop.call_soon(lambda: asyncio.ensure_future(acp.emit_available_commands_update()))

    async def _call_new_session(self, params: Dict[str, Any]) -> str:
        fn = getattr(self._agent_provider, "new_session", None)
        if callable(fn):
            try:
                sid = fn(params)
                if isinstance(sid, str) and sid:
                    return sid
            except Exception:
                pass
        return str(uuid.uuid4().hex)

    async def _call_void(self, name: str, *args: Any) -> None:
        fn = getattr(self._agent_provider, name, None)
        if callable(fn):
            try:
                fn(*args)
            except Exception:
                pass

    def _next_req_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _write(self, frame: Dict[str, Any]) -> None:
        if self._writer is None:
            return
        s = json.dumps(frame, ensure_ascii=False) + "\n"
        w = self._writer
        if isinstance(w, asyncio.StreamWriter):
            w.write(s.encode("utf-8"))
            try:
                await w.drain()
            except Exception:
                pass
        else:
            w.write(s)
            flush = getattr(w, "flush", None)
            if callable(flush):
                try:
                    flush()
                except Exception:
                    pass


# ============================================================ 流/重定向辅助
async def _iter_lines(reader: Any) -> Any:
    """统一产出逐行文本：asyncio.StreamReader 走 readline，同步文件走 executor。"""
    if isinstance(reader, asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            yield line.decode("utf-8", "replace") if isinstance(line, bytes) else line
    else:
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, reader.readline)
            if line == "":
                break
            yield line


def _redirect_console_to_stderr() -> None:
    """stdout 是协议通道，任何 console.* 写会破坏 NDJSON 流 —— 全部重定向 stderr。"""
    sink = lambda *a: sys.stderr.write(" ".join(str(x) for x in a) + "\n")
    sys.console = None  # type: ignore[attr-defined]
    import builtins
    builtins.print = sink  # 仅覆盖本进程 print；logging 由调用方配置


# ============================================================ 顶层入口
async def run_acp_server_stdio(server: AcpServer) -> None:
    """以 stdin/stdout 运行一个 AcpServer。"""
    await server.serve_stdio()
