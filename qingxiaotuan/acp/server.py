"""ACP server: 把青小团 Agent 暴露为 IDE 可驱动的 Agent Client Protocol 服务。

核心理念 (对齐 ACP 协议, 自研实现):
- 一个进程 = 一个 session; IDE 按工作目录拉起子进程, 通过 stdin/stdout 的
  NDJSON-JSON-RPC 驱动 agent。
- `initialize` 返回 session / agentInfo / model / tools / slash_commands / authMethods,
  并广播 `session/update`(initialized) 与 `available_commands_update` (对齐 ACP 的 session-scoped skills 思路)。
- `prompt` 异步执行: server 立即回 {} , 随后通过 `session/update`(agent_message_chunk /
  tool_call / tool_call_update / permission_request) 与 `task/update`(running/completed/failed)
  流式回报; 危险操作经 `confirm` 向 IDE 发起权限握手, 阻塞等待 `update`(permission_response)。
- `cancel` 调用底层 `agent.cancel()`; `shutdown` 优雅退出。

与上游参考实现的关键差异:
- 上游可委托 `@moonshot-ai/acp-adapter` / `acp-server` 实现; 此处用纯 Python 自研子集,
  并复用青小团既有 `Agent` (DIP: `agent_provider` 注入), 不引入 Node 依赖。
- 鉴权: 上游走 device-code `--login`; 此处 `authMethods` 默认空, 由青小团既有
  `qxt login` / provider 配置承担, 保留 `terminal` 入口约定位 (args:['--login'])。
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from .protocol import (
    ACP_CANCEL,
    ACP_INITIALIZE,
    ACP_PROMPT,
    ACP_SHUTDOWN,
    ACP_UPDATE,
    NOTIF_SESSION_UPDATE,
    NOTIF_TASK_UPDATE,
    build_initialize_result,
    build_notification,
    build_response,
    emit,
    read_messages,
    read_messages_robust,
)
from .version import CURRENT_VERSION, negotiate_version

# Agent 提供方: 给定 confirm 回调, 返回一个具备 run/cancel 的 AgentLike。
AgentProvider = Callable[[Callable[[str], bool]], Any]


class AcpServer:
    """Agent Client Protocol server (NDJSON-JSON-RPC over stdio)。"""

    def __init__(
        self,
        agent_provider: AgentProvider,
        agent_info: Dict[str, str],
        model: str,
        model_info: Dict[str, str],
        get_slash_commands: Optional[Callable[[], List[Dict[str, str]]]] = None,
        workspace: str = ".",
        tools: Optional[List[Dict[str, Any]]] = None,
        enhanced: bool = False,
    ) -> None:
        self._agent_provider = agent_provider
        self._agent_info = agent_info
        self._model = model
        self._model_info = model_info
        self._get_slash_commands = get_slash_commands or (lambda: [])
        self._workspace = workspace
        self._tools = tools or []
        # 融合层开关: 开启后补充版本协商 / 健壮帧解析 / 富事件 / 审批选项集。
        # 默认 False —— 原生单会话行为零改变。
        self._enhanced = enhanced

        self._session_id = uuid.uuid4().hex
        self._write_lock = threading.Lock()
        self._writer: Any = None

        self._running = False
        self._agent: Any = None
        self._active_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()

        # 权限握手: toolCallId -> {event, decision}
        self._perm_lock = threading.Lock()
        self._pending_perm: Dict[str, Dict[str, Any]] = {}
        self._last_tool_id: Optional[str] = None
        self._tid_counter = 0
        # 协商出的协议版本（enhanced 时由 initialize 填入）
        self._protocol_version: Optional[int] = None

    # -------------------------------------------------------------- 写通道

    def _write(self, msg: Dict[str, Any]) -> None:
        with self._write_lock:
            emit(self._writer, msg)

    def _session_update(self, **body: Any) -> None:
        self._write(build_notification(NOTIF_SESSION_UPDATE,
                                       {"sessionId": self._session_id, **body}))

    def _task_update(self, **body: Any) -> None:
        self._write(build_notification(NOTIF_TASK_UPDATE,
                                       {"sessionId": self._session_id, **body}))

    # -------------------------------------------------------------- 权限握手

    def _new_tid(self) -> str:
        self._tid_counter += 1
        return f"toolcall_{self._tid_counter}"

    def _confirm(self, question: str) -> bool:
        """向 IDE 请求权限, 阻塞直到收到 `update`(permission_response)。

        融合层 (enhanced): 在 ``permission_request`` 中附带可选决策集
        (``approve_once`` / ``approve_always`` / ``reject`` / ``plan_review``)，
        与 kernel ACP 的审批语义对齐；``plan_review`` 表示 IDE 已向用户呈现计划并获批准，
        原生无独立 plan 闸门，按批准处理。
        """
        tid = self._last_tool_id or self._new_tid()
        self._last_tool_id = tid
        ev = threading.Event()
        with self._perm_lock:
            self._pending_perm[tid] = {"event": ev, "decision": None}
        perm_body = {
            "type": "permission_request",
            "toolCallId": tid,
            "permission": "ask_user",
            "description": question[:500],
        }
        if self._enhanced:
            perm_body["options"] = [
                "approve_once", "approve_always", "reject", "plan_review"
            ]
        self._session_update(**perm_body)
        ev.wait()  # 由 _handle_update 释放
        with self._perm_lock:
            decision = self._pending_perm.pop(tid, {}).get("decision", "denied")
        self._session_update(type="permission_update", toolCallId=tid, decision=decision)
        # 批准语义: 原生 approved / 会话级 approved_for_session / 融合层 approve_once /
        # approve_always / plan_review 均视为放行；reject / denied 视为拒绝。
        return decision in (
            "approved", "approved_for_session",
            "approve_once", "approve_always", "plan_review",
        )

    def _on_token(self, delta: str) -> None:
        if delta:
            self._session_update(type="agent_message_chunk", text=delta)

    def _on_reason(self, text: str) -> None:
        """融合层富事件: 把模型推理过程作为 ``agent_thought_chunk`` 流式回报 (enhanced 时启用)。"""
        if text and self._enhanced:
            self._session_update(type="agent_thought_chunk", text=text)

    def _on_tool(self, name: str, args: str) -> None:
        tid = self._last_tool_id or self._new_tid()
        self._last_tool_id = tid
        self._session_update(
            type="tool_call",
            toolCallId=tid,
            toolCall={"name": name},
            description=(args or "")[:500],
        )

    def _on_tool_result(self, name: str, result: str) -> None:
        tid = self._last_tool_id
        self._session_update(
            type="tool_call_update",
            toolCallId=tid,
            status="completed",
            result=(result or "")[:1500],
        )
        self._last_tool_id = None

    def _on_error(self, message: str) -> None:
        self._task_update(status="failed", error=message[:500])

    # -------------------------------------------------------------- 主循环

    def serve_stdio(self) -> None:
        import sys

        self.serve(sys.stdin, sys.stdout)

    def serve(self, reader, writer) -> None:
        self._writer = writer
        # 融合层: enhanced 模式用错误容忍的增量帧解析（LineBuffer），对畸形帧更健壮。
        messages = read_messages_robust(reader) if self._enhanced else read_messages(reader)
        try:
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                method = msg.get("method")
                req_id = msg.get("id")
                params = msg.get("params") or {}
                handler = self._dispatch.get(method)
                if handler is None:
                    self._write(build_response(
                        req_id, error={"code": -32601, "message": f"Method not found: {method}"}))
                    continue
                try:
                    handler(params, req_id)
                except Exception as exc:  # noqa: BLE001
                    self._write(build_response(
                        req_id, error={"code": -32000, "message": str(exc)}))
                if method == ACP_SHUTDOWN:
                    break
        finally:
            self._shutdown.set()

    @property
    def _dispatch(self):
        return {
            ACP_INITIALIZE: self._handle_initialize,
            ACP_PROMPT: self._handle_prompt,
            ACP_CANCEL: self._handle_cancel,
            ACP_UPDATE: self._handle_update,
            ACP_SHUTDOWN: self._handle_shutdown,
        }

    # -------------------------------------------------------------- 方法处理

    def _handle_initialize(self, params: Dict[str, Any], req_id: Any) -> None:
        slash = self._get_slash_commands()
        # 融合层: enhanced 模式做协议版本协商（不破坏无 protocolVersion 的旧客户端）。
        protocol_version = None
        if self._enhanced:
            client_ver = params.get("protocolVersion")
            if isinstance(client_ver, int):
                protocol_version = negotiate_version(client_ver)
            else:
                protocol_version = CURRENT_VERSION
            self._protocol_version = protocol_version
        self._write(build_response(req_id, build_initialize_result(
            session_id=self._session_id,
            agent_info=self._agent_info,
            model=self._model,
            model_info=self._model_info,
            tools=self._tools,
            slash_commands=slash,
            workspace_folder=self._workspace,
            auth_methods=[],  # 由 qxt login / provider 配置承担
            protocol_version=protocol_version,
        )))
        # 广播初始化完成 + 可用命令快照 (kimi 的 available_commands_update)
        self._session_update(type="initialized")
        self._session_update(type="available_commands_update", commands=slash)

    def _handle_prompt(self, params: Dict[str, Any], req_id: Any) -> None:
        if self._running:
            self._write(build_response(
                req_id, error={"code": -32000, "message": "A prompt is already running"}))
            return
        prompt_text = params.get("prompt", "")
        self._write(build_response(req_id, {}))
        self._running = True
        self._task_update(status="running")
        thread = threading.Thread(
            target=self._run_agent, args=(prompt_text,), daemon=True)
        self._active_thread = thread
        thread.start()

    def _run_agent(self, prompt_text: str) -> None:
        try:
            self._agent = self._agent_provider(self._confirm)
            # 融合层富事件: enhanced 时把模型推理过程作为 agent_thought_chunk 回报。
            # 仅在 enhanced 模式下把 on_reason 传给 agent.run —— 保持原生契约不变,
            # 既有的非融合 Agent（不接收 on_reason 参数）继续零改造可用。
            run_kwargs: Dict[str, Any] = dict(
                stream=True,
                on_token=self._on_token,
                on_tool=self._on_tool,
                on_tool_result=self._on_tool_result,
                on_error=self._on_error,
                session_id=self._session_id,
            )
            if self._enhanced:
                run_kwargs["on_reason"] = self._on_reason
            answer = self._agent.run(prompt_text, **run_kwargs)
            self._task_update(status="completed", result=(answer or "")[:1500])
        except Exception as exc:  # noqa: BLE001
            self._task_update(status="failed", error=str(exc)[:500])
        finally:
            self._running = False
            self._agent = None
            self._active_thread = None

    def _handle_cancel(self, params: Dict[str, Any], req_id: Any) -> None:
        if self._agent is not None and hasattr(self._agent, "cancel"):
            self._agent.cancel()
        self._write(build_response(req_id, {}))

    def _handle_update(self, params: Dict[str, Any], req_id: Any) -> None:
        """client -> server: 权限回执 / 斜杠命令。"""
        utype = params.get("type")
        if utype == "permission_response":
            tid = params.get("toolCallId") or ""
            decision = params.get("decision", "denied")
            with self._perm_lock:
                pending = self._pending_perm.get(tid)
            if pending is not None:
                pending["decision"] = decision
                pending["event"].set()
        # 其它 update 类型 (command 等) 暂不做服务端处理, 保留扩展点。
        self._write(build_response(req_id, {}))

    def _handle_shutdown(self, params: Dict[str, Any], req_id: Any) -> None:
        if self._agent is not None and hasattr(self._agent, "cancel"):
            self._agent.cancel()
        self._write(build_response(req_id, {}))
        self._shutdown.set()
