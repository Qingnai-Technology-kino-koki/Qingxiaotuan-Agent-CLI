"""青小团装配器: 构建内核/Agent/内置技能。

延迟索引: 不在 create_agent 时同步扫描工作区, 避免启动被 KeyboardInterrupt 打断。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from .core.kernel import Kernel
from .config import Config
from .core.agent import Agent
from .config.plugin import ConfigPlugin
from .tools import (
    ToolRegistryPlugin, FilesystemPlugin, ShellPlugin, WebPlugin, CodeToolPlugin,
    MemoryToolPlugin, SkillToolPlugin, LanguagePlugin, ExternalToolsPlugin,
    DispatchPlugin, PipelinePlugin, CodeReviewPlugin, CheckpointPlugin,
    TaskToolPlugin, SessionToolsPlugin,
)
from .memory.plugin import MemoryPlugin, SessionPlugin
from .skills import SkillManager
from .skills.plugin import SkillPlugin
from .context.plugin import IndexerPlugin
from .models.plugin import ModelPlugin
from .cron import CronPlugin
from .self_improve import SelfImprovePlugin
from .audit.plugin import AuditPlugin
from .tools.mcp import MCPPlugin
from .tools.subagent_tool import SubagentPlugin
from .tools.messaging_tool import MessagingPlugin
from .tools.workflow_tool import WorkflowPlugin
from .tools.artifact_tool import ArtifactPlugin

logger = logging.getLogger(__name__)


def build_kernel(profile: str = "default", patch_file: Optional[str] = None) -> Kernel:
    """构建微内核, 注册所有插件。"""
    kernel = Kernel()

    # 核心插件: 配置 -> 工具 -> 模型 -> 记忆 -> 技能 -> 上下文 -> 定时 -> 审计 -> 自我改进
    kernel.register(ConfigPlugin())
    kernel.register(ToolRegistryPlugin())
    kernel.register(FilesystemPlugin())
    kernel.register(ShellPlugin())
    kernel.register(WebPlugin())
    kernel.register(CodeToolPlugin())
    kernel.register(MemoryToolPlugin())
    kernel.register(SkillToolPlugin())
    kernel.register(LanguagePlugin())
    kernel.register(ExternalToolsPlugin())
    kernel.register(DispatchPlugin())
    kernel.register(PipelinePlugin())
    kernel.register(CodeReviewPlugin())
    kernel.register(CheckpointPlugin())
    kernel.register(TaskToolPlugin())
    kernel.register(SessionToolsPlugin())
    kernel.register(MemoryPlugin())
    kernel.register(SessionPlugin())
    kernel.register(SkillPlugin())
    kernel.register(ModelPlugin())
    kernel.register(IndexerPlugin())
    kernel.register(CronPlugin())
    kernel.register(AuditPlugin())
    kernel.register(SelfImprovePlugin())
    kernel.register(MCPPlugin())
    kernel.register(SubagentPlugin())
    kernel.register(MessagingPlugin())
    kernel.register(WorkflowPlugin())
    kernel.register(ArtifactPlugin())

    kernel.activate_all()

    config: Config = kernel.require("config")
    # 首次启动播种内置技能 (幂等, 同名不覆盖)
    seed_builtin_skills(config)

    return kernel


def create_agent(
    kernel: Kernel,
    workspace: str,
    confirm=None,
    exclude_tools=None,
    indexer=None,
    system_extra: str = "",
) -> Agent:
    """创建 Agent (索引延迟构建, 不阻塞启动)。"""
    config: Config = kernel.require("config")
    if indexer is None and config.get("context.auto_index", True):
        try:
            from .context.indexer import CodebaseIndexer
            indexer = CodebaseIndexer(
                workspace,
                max_files=config.get("context.index_max_files", 300),
                max_loc=config.get("context.index_max_loc", 200_000),
            )
            kernel.unprovide("codebase_indexer")
            kernel.provide("codebase_indexer", indexer, owner="app")
        except Exception:  # noqa: BLE001
            indexer = None
    agent = Agent(
        kernel=kernel,
        config=config,
        workspace=workspace,
        confirm=confirm,
        exclude_tools=exclude_tools,
        indexer=indexer,
        system_extra=system_extra,
    )
    # 事务化操作账本: 给 Agent 的 ToolContext 挂上 MutationLedger,
    # 写类工具执行前自动快照、异常自动回滚、支持精细 undo (最小影响半径的事后可逆闭环)。
    from .core.ledger import MutationLedger
    agent.ctx.ledger = MutationLedger(workspace, config)
    # 用户级 Hooks: 让用户在工具执行前/后挂载脚本, 把 Agent 变成可编排的。
    # 安全: 命令强制 list(argv), 超时强杀, 阻断/改参权需显式声明 (fail-safe, 不阻断)。
    try:
        from .hooks.manager import HookManager
        agent.ctx.hooks = HookManager(config, workspace, kernel=kernel)
    except Exception:  # noqa: BLE001
        agent.ctx.hooks = None
    # 自定义斜杠命令: 把 <home>/commands 与 <workspace>/.qxt/commands 挂进分发链
    # (运行期包装 _handle_slash, 幂等; 失败不影响主流程)。延迟导入避免 cli <-> app 环。
    try:
        from .cli.user_commands import install_user_commands
        install_user_commands(agent, config, workspace)
    except Exception as exc:  # noqa: BLE001
        logger.debug("自定义斜杠命令安装失败: %s", exc)
    return agent


def _builtin_skills_dir() -> Path:
    """内置技能单一权威源: resources/skills/builtin/ (打包后随 wheel 分发)。"""
    try:
        from importlib import resources

        with resources.as_file(resources.files("qingxiaotuan.resources") / "skills" / "builtin") as p:
            return p
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parent / "resources" / "skills" / "builtin"


def seed_builtin_skills(config_or_kernel) -> int:
    """把内置技能首次 seed 到用户技能目录 (同名不覆盖, 尊重用户后续改进)。

    兼容两种入参: Config 直接使用, Kernel 则从中取出 config。
    """
    if hasattr(config_or_kernel, "require"):
        config = config_or_kernel.require("config")
    else:
        config = config_or_kernel
    manager = SkillManager(config.home)
    src = _builtin_skills_dir()
    if not src.is_dir():
        return 0
    seeded = 0
    for path in sorted(src.glob("*.md")):
        dest = manager.dir / path.name
        if dest.exists():
            continue
        shutil.copy(path, dest)
        seeded += 1
    return seeded
