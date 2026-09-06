"""`qxt acp` —— 以 Agent Client Protocol server 身份运行, 供 IDE (VS Code/Zed/JetBrains) 驱动。

自研实现，接口对齐 kimi-code `acp.ts` / `acp-native.ts` 的理念:
- 通过 stdin/stdout 的 NDJSON-JSON-RPC 暴露青小团 Agent。
- IDE 侧按工作目录拉起本进程, 通过 `initialize`/`prompt`/`update`/`cancel`/`shutdown` 驱动。
- 危险操作经 ACP 的 `permission_request` 向 IDE 发起权限握手 (kimi 的 terminal-auth 思路)。

与 kimi 的差异: kimi 复用 `@moonshot-ai/acp-adapter`; 此处为纯 Python 重写,
复用青小团既有 `Agent` (DIP: 通过 agent_provider 注入), 不引入 Node 依赖。
"""

from __future__ import annotations

import os
import sys

from .. import __version__
from ..acp.server import AcpServer
from ..config import Config
from ..i18n import ensure_language
from ..logging_conf import log


# ---- app 链惰性加载: 仅真正启动 ACP 会话时才构建内核 ----
def build_kernel(*a, **k):
    from ..app import build_kernel as _f
    return _f(*a, **k)


def create_agent(*a, **k):
    from ..app import create_agent as _f
    return _f(*a, **k)


def seed_builtin_skills(*a, **k):
    from ..app import seed_builtin_skills as _f
    return _f(*a, **k)


def _get_slash_commands() -> list:
    """尽力列举内置 + 用户斜杠命令 (失败则降级为空列表)。"""
    try:
        from .cmd_slash import list_slash_commands

        return list_slash_commands()
    except Exception as exc:  # noqa: BLE001
        log.debug("ACP 列举斜杠命令失败: %s", exc)
        return []


def cmd_acp(args) -> int:
    """ACP server 入口。"""
    # ACP 客户端常以 `<binary> --login` 触发终端鉴权 (kimi 的 AuthMethodTerminal)。
    if getattr(args, "login", False):
        try:
            from .cmd_setup import cmd_setup

            return cmd_setup(args)
        except Exception:  # noqa: BLE001
            sys.stderr.write(
                "青小团 ACP 终端鉴权: 请改用 `qxt setup` 配置模型供应商与密钥。\n"
            )
            return 0

    try:
        kernel = build_kernel()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ACP server 启动失败: {exc}\n")
        return 1

    config: Config = kernel.require("config")
    ensure_language(config)
    seed_builtin_skills(kernel)

    workspace = getattr(args, "workspace", None) or os.getcwd()
    provider = config.get("model.provider", "unknown")
    model = config.get("model.model", "unknown")

    agent_info = {"name": "青小团 Qingxiaotuan CLI", "version": __version__}
    model_info = {"id": model, "provider": provider}
    model_label = f"{provider}/{model}"

    def make_agent(confirm):
        return create_agent(kernel, workspace, confirm=confirm)

    server = AcpServer(
        agent_provider=make_agent,
        agent_info=agent_info,
        model=model_label,
        model_info=model_info,
        get_slash_commands=_get_slash_commands,
        workspace=workspace,
        # 融合层: fusion.acp_enhanced 开启后补充版本协商/健壮帧解析/富事件/审批选项集。
        enhanced=config.get("fusion.acp_enhanced", False),
    )
    try:
        server.serve_stdio()
    except KeyboardInterrupt:
        pass
    return 0
