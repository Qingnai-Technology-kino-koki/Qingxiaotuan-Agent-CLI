"""定时任务子系统。"""

from .store import CronStore

__all__ = ["CronStore", "CronPlugin"]


def __getattr__(name: str):
    # plugin 依赖 core/tools 全链 (~400ms), 惰性加载: 仅构建内核或显式访问时导入。
    if name == "CronPlugin":
        from .plugin import CronPlugin
        return CronPlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
