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
    TaskToolPlugin, SessionToolsPlugin, CodeGraphPlugin, BackendDevPlugin, SandboxPlugin,
)
from .memory.plugin import MemoryPlugin, SessionPlugin
from .skills import SkillManager
from .skills.plugin import SkillPlugin
from .context.plugin import IndexerPlugin
from .models.plugin import ModelPlugin
from .cron import CronPlugin
from .self_improve import SelfImprovePlugin
from .audit.plugin import AuditPlugin
from .codedev import CodeDevPlugin
from .tools.mcp import MCPPlugin
from .tools.subagent_tool import SubagentPlugin
from .tools.messaging_tool import MessagingPlugin
from .tools.workflow_tool import WorkflowPlugin
from .tools.artifact_tool import ArtifactPlugin
from .core.security_plugin import SecurityPlugin
from .core.collaboration_plugin import CollaborationPlugin
from .arch.plugin import ArchPlugin

logger = logging.getLogger(__name__)


def build_kernel(profile: str = "default", patch_file: Optional[str] = None) -> Kernel:
    """构建微内核, 注册所有插件。"""

    def _kernel_patch_plugin():
        # 惰性: 仅在内核构建层面需要时才 import 补丁层, 避免拖累纯 CLI 冷启动
        from .kernel_patch.plugin import KernelPatchPlugin
        return KernelPatchPlugin()

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
    # 代码开发工具集: 依赖图 / 后端模板生成 / 沙箱执行 (此前未注册, 模型拿不到)
    kernel.register(CodeGraphPlugin())
    kernel.register(BackendDevPlugin())
    kernel.register(SandboxPlugin())
    # 代码开发子系统: 检索增强 + 验证闸门 + 规格分解编排（对标 Claude Code 的底层能力, 非 loop）
    kernel.register(CodeDevPlugin())
    # 安全子系统插件: 分类器/网络守卫/事件总线/MCP加固
    kernel.register(SecurityPlugin())
    # 多 Agent 协作子系统插件: 协作协议/角色注册/结果聚合
    kernel.register(CollaborationPlugin())
    # 五层架构插件: 安全/执行/编排/上下文/可观测 (可插拔, 一等公民服务)
    kernel.register(ArchPlugin())
    # 内核补丁层 (扩展增强模块层): 对现有实现注入可回滚/可审计补丁
    kernel.register(_kernel_patch_plugin())

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
    # 自动检查点存储: 每次写工具成功后自动建点 (30天TTL / 三恢复模式 / 摘要),
    # 依托账本快照, 跨会话持久化于 .qxt/checkpoints/。注册为内核服务供工具执行器勾取。
    try:
        from .core.checkpoint_store import CheckpointStore
        existing = kernel.get("checkpoint_store")
        if existing is None:
            _cps = CheckpointStore(workspace, agent.ctx.ledger, config)
            kernel.provide("checkpoint_store", _cps, owner="app")
        else:
            _cps = existing
        agent.ctx.checkpoint_store = _cps
    except Exception:  # noqa: BLE001
        agent.ctx.checkpoint_store = None
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
