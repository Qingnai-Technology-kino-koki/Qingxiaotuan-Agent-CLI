"""应用装配 —— 把所有插件挂到微内核上 (Harness 的 profile 组装对应物)。"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import shutil
from pathlib import Path
from typing import List, Optional

from .config.loader import Config, load_dotenv
from .config.plugin import ConfigPlugin
from .context.indexer import CodebaseIndexer
from .context.manager import ContextManager
from .context.plugin import IndexerPlugin
from .core.agent import Agent
from .core.kernel import Kernel, Plugin
from .cron.plugin import CronPlugin
from .logging_conf import setup_logging, log
from .memory import MemoryStore, SessionStore
from .memory.plugin import MemoryPlugin, SessionPlugin
from .models import create_adapter
from .models.plugin import ModelPlugin
from .skills import SkillManager
from .skills.plugin import SkillPlugin
from .self_improve.plugin import SelfImprovePlugin
from .tools import ToolRegistryPlugin, builtin_tool_plugins
from .tools.mcp.plugin import MCPPlugin
from .models.router import ModelRouter, CostTracker

# 内置世界顶级技能: 单一权威源在 resources/skills/builtin/ (打包后随 wheel 分发)
def _builtin_skills_dir() -> Path:
    try:
        from importlib import resources

        with resources.as_file(resources.files("qingxiaotuan.resources") / "skills" / "builtin") as p:
            return p
    except Exception:
        return Path(__file__).resolve().parent / "resources" / "skills" / "builtin"


def seed_builtin_skills(config: Config) -> int:
    """把内置技能首次 seed 到用户技能目录 (同名不覆盖, 尊重用户后续改进)。"""
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


def build_kernel(profile: str = "default", patch_file: Optional[str] = None) -> Kernel:
    """组装一个完整可用的青小团实例。"""
    config = Config(profile=profile, patch_file=patch_file)
    config.ensure_home()
    load_dotenv(config.home)
    # 重新读一次配置 (.env 可能影响 api_key_env 等, 但此处仅加载日志即可)
    setup_logging(
        config.home,
        level=logging.INFO if not config.get("ui.verbose_log", False) else logging.DEBUG,
        audit=config.get("model.audit", False),
    )
    log.info("启动青小团 · profile=%s · model=%s/%s", profile,
             config.get("model.provider"), config.get("model.model"))

    kernel = Kernel()
    kernel.register(ConfigPlugin(config))
    kernel.register(ModelPlugin())
    kernel.register(MemoryPlugin())
    kernel.register(SkillPlugin())
    kernel.register(SessionPlugin())
    kernel.register(IndexerPlugin())
    kernel.register(ToolRegistryPlugin())
    for plugin in builtin_tool_plugins():
        kernel.register(plugin)
    kernel.register(CronPlugin())
    kernel.register(MCPPlugin())
    kernel.register(SelfImprovePlugin())
    load_external_plugins(kernel)
    kernel.activate_all()

    # 注册 CostTracker 到内核 (供成本追踪使用)
    cost_tracker = CostTracker()
    kernel.provide("cost_tracker", cost_tracker, owner="app")

    # 注册 ModelRouter 到内核 (供智能路由使用)
    router = ModelRouter(
        default_provider=config.get("model.provider", "deepseek"),
        default_model=config.get("model.model", "deepseek-chat"),
        budget_limit=config.get("router.budget_limit", 0.0),
    )
    kernel.provide("model_router", router, owner="app")

    # 首次启动: 把内置世界顶级技能 seed 到用户技能目录
    try:
        seed_builtin_skills(config)
    except Exception:  # noqa: BLE001
        pass
    return kernel


def load_external_plugins(kernel: Kernel) -> List[str]:
    """从 ~/.qingxiaotuan/plugins/*.py 热加载第三方插件 (一切皆插件的落实)。

    每个 .py 文件中可定义任意数量的 ``Plugin`` 子类; 本函数把它们注册进内核。
    插件与内置插件地位平等, 同样要走激活/依赖解析流程。
    """
    config = kernel.get("config")
    if config is None:
        return []
    plugin_dir = config.home / "plugins"
    if not plugin_dir.is_dir():
        return []
    loaded: List[str] = []
    for path in sorted(plugin_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"_qxt_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            kernel.emit("plugin.load_error", {"path": str(path), "error": str(exc)})
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Plugin) and obj is not Plugin and obj.__module__ == module.__name__:
                try:
                    kernel.register(obj())
                    loaded.append(path.stem)
                except Exception as exc:  # noqa: BLE001
                    kernel.emit("plugin.register_error", {"name": obj.__name__, "error": str(exc)})
    return loaded


def create_agent(kernel: Kernel, workspace: str, confirm=None, exclude_tools=()) -> Agent:
    config = kernel.require("config")
    # 工作区索引器: 供 Agent 系统提示钉地图 + 供 code 工具复用 (缓存)
    indexer = None
    if config.get("context.auto_index", True):
        try:
            indexer = CodebaseIndexer(
                workspace,
                max_files=config.get("context.index_max_files", 300),
                max_loc=config.get("context.index_max_loc", 200_000),
            )
            # 覆盖 IndexerPlugin 注册的 None 占位 (按 workspace 实建索引器)
            kernel.unprovide("codebase_indexer")
            kernel.provide("codebase_indexer", indexer, owner="app")
        except Exception:  # noqa: BLE001
            indexer = None
    return Agent(kernel=kernel, config=config, workspace=workspace,
                 confirm=confirm, exclude_tools=exclude_tools, indexer=indexer)
