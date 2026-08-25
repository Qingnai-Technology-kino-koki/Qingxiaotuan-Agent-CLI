"""配置插件 —— 把 Config 实例注册为内核服务。"""

from __future__ import annotations

from ..core.kernel import Kernel, Plugin
from .loader import Config


class ConfigPlugin(Plugin):
    name = "config"
    provides = ["config"]

    def __init__(self, config: Config = None) -> None:
        super().__init__()
        self.config = config or Config()

    def activate(self, kernel: Kernel) -> None:
        kernel.provide("config", self.config, owner=self.name)
