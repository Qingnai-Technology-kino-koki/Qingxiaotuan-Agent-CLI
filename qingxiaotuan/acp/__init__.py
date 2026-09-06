"""青小团 ACP (Agent Client Protocol) 集成（自研实现）。

设计基准 (对齐公开的 ACP 规范, 用 Python 从架构到接口自研):
- 将 agent 作为 ACP server 通过 stdio 暴露, 供 VS Code / Zed / JetBrains 等
  IDE 驱动; 支持 terminal 鉴权 (`args:['--login']`) 与 `available_commands_update`。
- opencode / Zed 公开的 ACP 规范: `initialize` / `prompt` / `update` / `cancel` /
  `shutdown` 五条方法, 以及 `session/update`、`task/update` 两类通知。

设计原则 (自研实现, 接口为 Python 风格):
- 一个 server 进程只服务一个 session (IDE 侧按目录拉起子进程)。
- 所有 I/O 走 newline-delimited JSON-RPC over stdio, 与 IDE 解耦。
- 复用现有 `Agent` 的 `run(prompt, stream, on_token, on_tool, on_tool_result,
  on_error)` + `cancel()` 接口, 不重复造轮子 (DIP: 通过 `agent_provider` 注入)。
- 危险操作由 `confirm` 回调向 IDE 发起 `permission_request`, 阻塞等待
  `update`(permission_response) 回执 —— 即 ACP 的 terminal-auth / 权限握手。

许可证与归属: 本协议子集为青小团对 ACP 理念的自研实现, 仅对协议做接口对齐。
"""

from .protocol import (
    ACP_INITIALIZE,
    ACP_PROMPT,
    ACP_UPDATE,
    ACP_CANCEL,
    ACP_SHUTDOWN,
    NOTIF_SESSION_UPDATE,
    NOTIF_TASK_UPDATE,
    build_initialize_result,
    emit,
    read_messages,
)
from .server import AcpServer

# 桥接：复用自研 kernel.acp（接口对齐 kimi-code acp-server），
# 以独立命名导出避免与既有 AcpServer 冲突，供 cmd_acp / 新入口按需切换。
from ..kernel.acp import (
    AcpInteractionBridge as KernelAcpInteractionBridge,
    AcpServer as KernelAcpServer,
    AcpSession as KernelAcpSession,
    KernelAcpBridge,
    request_permission as kernel_request_permission,
    run_acp_server_stdio,
    session_update as kernel_session_update,
)

__all__ = [
    "AcpServer",
    "ACP_INITIALIZE",
    "ACP_PROMPT",
    "ACP_UPDATE",
    "ACP_CANCEL",
    "ACP_SHUTDOWN",
    "NOTIF_SESSION_UPDATE",
    "NOTIF_TASK_UPDATE",
    "build_initialize_result",
    "emit",
    "read_messages",
    # kernel.acp 桥接（新 ACP 子系统）
    "KernelAcpServer",
    "KernelAcpSession",
    "KernelAcpInteractionBridge",
    "KernelAcpBridge",
    "kernel_session_update",
    "kernel_request_permission",
    "run_acp_server_stdio",
]
