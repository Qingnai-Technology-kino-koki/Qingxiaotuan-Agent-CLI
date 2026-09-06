"""把青小团既有实现适配成本模块的 ``agent_provider`` 接口（DIP 桥）。

目标：让 ``qingxiaotuan/acp/cmd_acp.py`` 等既有入口可复用本 ``kernel.acp`` 子系统，
而不必把既有 ACP server 推倒重来。

适配来源（按可用性）：
- 首选 ``qingxiaotuan.core.agent.Agent``：其 ``run(prompt, stream, on_token, on_tool,
  on_tool_result, on_error)`` 是同步 + 回调式；本桥在 executor 里跑它，把回调推入
  asyncio.Queue，再包成异步事件源（见 :class:`KernelAcpBridge.prompt`）。
- 兼容 ``qingxiaotuan.acp.server.AcpServer``：若调用方已持有其实例，可用
  :func:`from_existing_acp_server` 包成 ``agent_provider``（精简桥，懒加载 + 降级）。

所有重量级导入均惰性进行，保证 ``import qingxiaotuan.kernel.acp`` 不触发内核/provider 依赖。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .events_map import acp_tool_call_id


class KernelAcpBridge:
    """把青小团既有 ``Agent`` 适配为 ``agent_provider``（prompt/cancel/available_commands）。

    用法::

        bridge = KernelAcpBridge(make_agent=make_agent, workspace=".")
        server = AcpServer(bridge, agent_info=...)

    ``make_agent`` 为 0 参 callable，返回一个具备 ``run(...)`` 与 ``cancel()`` 的 AgentLike。
    若不传，桥会惰性 ``create_agent(kernel, workspace)``。
    """

    def __init__(
        self,
        make_agent: Optional[Callable[[], Any]] = None,
        *,
        kernel: Any = None,
        workspace: str = ".",
    ) -> None:
        self._make_agent = make_agent
        self._kernel = kernel
        self._workspace = workspace
        self._agent: Any = None
        self._turn_tool_ids: Dict[int, str] = {}
        self._mode: Any = None
        self._model: str = ""

    # ----------------------------------------------------------- agent 惰性构建
    def _ensure_agent(self) -> Any:
        if self._agent is not None:
            return self._agent
        if self._make_agent is not None:
            self._agent = self._make_agent()
            return self._agent
        # 降级：惰性复用既有 app.create_agent
        from ..app import create_agent  # type: ignore

        self._agent = create_agent(self._kernel, self._workspace)
        return self._agent

    # ----------------------------------------------------------- agent_provider 接口
    async def prompt(
        self, session_id: str, input_text: str, signal: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """驱动一次 prompt，产出引擎事件流（供 AcpSession 翻译）。"""
        agent = self._ensure_agent()
        turn_id = 1
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        tool_ids: Dict[str, str] = {}

        def on_token(delta: str) -> None:
            queue.put_nowait({"type": "assistant.delta", "delta": delta or ""})

        def on_tool(name: str, args: str) -> None:
            raw_id = f"tc{len(tool_ids) + 1}"
            tool_ids[raw_id] = raw_id
            queue.put_nowait({
                "type": "tool.call.started", "turn_id": turn_id, "tool_call_id": raw_id,
                "name": name, "args": args,
            })

        def on_tool_result(name: str, result: str) -> None:
            raw_id = next(iter(tool_ids), "tc1")
            queue.put_nowait({
                "type": "tool.result", "turn_id": turn_id, "tool_call_id": raw_id,
                "output": result or "", "is_error": False,
            })

        def on_error(msg: str) -> None:
            queue.put_nowait({
                "type": "turn.ended", "reason": "failed",
                "error": {"code": "agent.error", "message": msg},
            })

        async def agen() -> AsyncIterator[Dict[str, Any]]:
            loop = asyncio.get_event_loop()
            run_fut = loop.run_in_executor(
                None,
                lambda: agent.run(
                    input_text, stream=True, on_token=on_token, on_tool=on_tool,
                    on_tool_result=on_tool_result, on_error=on_error,
                ),
            )
            done = False
            while not done:
                get_task = asyncio.ensure_future(queue.get())
                done_task, pending = await asyncio.wait(
                    {run_fut, get_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if get_task in done_task:
                    item = get_task.result()
                    yield item
                    if item.get("type") == "turn.ended":
                        done = True
                else:
                    # agent 已结束：先吐完队列余量，再补一个正常 turn.ended
                    get_task.cancel()
                    while not queue.empty():
                        rem = queue.get_nowait()
                        yield rem
                        if rem.get("type") == "turn.ended":
                            done = True
                    if not done:
                        yield {"type": "turn.ended", "reason": "completed"}
                    done = True
            # 取 run 结果以吸收异常（不阻塞）
            try:
                await run_fut
            except Exception:
                pass

        async for ev in agen():
            yield ev

    def cancel(self, session_id: str, turn_id: int) -> None:
        agent = self._agent
        if agent is not None and hasattr(agent, "cancel"):
            try:
                agent.cancel()
            except Exception:
                pass

    def available_commands(self) -> List[Dict[str, Any]]:
        try:
            from ..cli.cmd_slash import list_slash_commands

            return [{"name": c.get("name", "")} for c in (list_slash_commands() or [])]
        except Exception:
            return []

    def list_sessions(self) -> List[str]:
        return []

    def new_session(self, params: Dict[str, Any]) -> str:
        import uuid

        return str(uuid.uuid4().hex)

    def set_mode(self, session_id: str, mode: Any) -> None:
        """接受 ACP 会话的 mode 偏好。

        桥接器把 ``prompt`` 直接委托给内核的 ``Agent``, 会话的运行模式由
        Agent 自身的策略驱动, 此处记录偏好但不强制透传, 保持单会话语义一致。
        幂等; 无效 mode 忽略。
        """
        self._mode = mode

    def set_model(self, session_id: str, model_id: str) -> None:
        """接受 ACP 会话的模型偏好。

        模型选择由内核 model_router 在 prompt 时解析, 邀请设置的 model_id 会
        被记录以支持将来按会话路由; 传入空串视为清除偏好。
        """
        self._model = model_id


def from_existing_acp_server(acp_server: Any) -> Any:
    """把既有 ``qingxiaotuan.acp.server.AcpServer`` 包成 agent_provider（精简桥）。

    既有 server 是线程式、按 stdin/stdout 驱动的完整 ACP server，与本项目单进程多 session
    模型不同；此处仅做最小兼容：作为 ``prompt`` 入口时降级为直接 new agent 运行。
    优先使用 KernelAcpBridge 以获得完整异步事件流。
    """
    make_agent = getattr(acp_server, "_agent_provider", None)
    if callable(make_agent):
        return KernelAcpBridge(make_agent=make_agent)
    return KernelAcpBridge()
