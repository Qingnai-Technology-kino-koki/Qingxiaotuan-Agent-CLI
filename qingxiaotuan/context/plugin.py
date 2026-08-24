"""上下文索引插件 —— 占位提供代码库索引器服务 (实际实例在 create_agent 时按 workspace 实建)。"""

from __future__ import annotations

from ..core.kernel import Kernel, Plugin


class IndexerPlugin(Plugin):
    name = "context.indexer"
    provides = ["codebase_indexer"]
    requires = ["config"]

    def activate(self, kernel: Kernel) -> None:
        # 索引器依赖工作区, 在 create_agent 时按 workspace 实建并覆盖此占位。
        kernel.provide("codebase_indexer", None, owner=self.name)
