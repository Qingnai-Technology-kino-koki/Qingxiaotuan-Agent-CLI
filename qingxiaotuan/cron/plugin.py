"""定时任务插件 —— 把 CronStore 注册为内核服务。"""

from __future__ import annotations

from ..core.kernel import Kernel, Plugin
from .store import CronStore


class CronPlugin(Plugin):
    name = "cron"
    provides = ["cron_store"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("cron.enabled", True):
            return
        kernel.provide("cron_store", CronStore(config.home), owner=self.name)
