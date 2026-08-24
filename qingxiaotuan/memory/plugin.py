"""记忆插件 —— 注册长期记忆与会话存储服务。"""

from __future__ import annotations

from ..core.kernel import Kernel, Plugin
from .store import MemoryStore
from .sessions import SessionStore


class MemoryPlugin(Plugin):
    name = "memory"
    provides = ["memory_store"]
    requires = ["config"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.require("config")
        store = MemoryStore(config.home, fts_enabled=config.get("memory.fts_enabled", True))
        kernel.provide("memory_store", store, owner=self.name)


class SessionPlugin(Plugin):
    name = "session"
    provides = ["session_store"]
    requires = ["config"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.require("config")
        kernel.provide("session_store", SessionStore(config.home), owner=self.name)
