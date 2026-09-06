"""scheduler —— 顺序/限量执行工具的调度器。

核心：``ToolScheduler.add(task)`` 时按 ``ToolAccesses.conflict()`` 判定并发还是排队。
冲突（写串行、路径重叠串行）则等待，否则立即与正在运行的任务并行。

用 asyncio 等价实现：
- ``active`` 列表保存「正在运行」的任务（其 run() 已被 create_task 调度）。
- ``queued`` 保存因冲突而等待的任务。
- 任一任务完成后从 active 移除，并尝试启动 queued 中不再冲突的任务。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, List, TypeVar

from .contract import ToolAccesses

T = TypeVar("T")


@dataclass
class SchedulerTask(Generic[T]):
    """调度单元：声明自己的资源访问 + 一个返回结果协程的工厂。"""

    accesses: ToolAccesses
    run: Callable[[], Awaitable[T]]


@dataclass
class _Scheduled:
    task: SchedulerTask
    future: "asyncio.Future"


class ToolScheduler(Generic[T]):
    """细粒度工具调度器。"""

    def __init__(self) -> None:
        self._active: List[_Scheduled] = []
        self._queued: List[_Scheduled] = []

    # ---- 对外 API ----------------------------------------------------------

    def add(self, task: SchedulerTask[T]) -> "asyncio.Future":
        """提交一个任务，返回其完成 future。

        若与已激活或已排队（在其之前）的任务冲突则排队，否则立即开始。
        """
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        scheduled = _Scheduled(task=task, future=future)
        if self._is_blocked(task, self._queued):
            self._queued.append(scheduled)
        else:
            self._start(scheduled)
        return future

    async def run_batch(self, tasks: List[SchedulerTask[T]]) -> List[T]:
        """调度一批任务并等待全部完成，按输入顺序返回结果。"""
        futures = [self.add(t) for t in tasks]
        return list(await asyncio.gather(*futures))

    # ---- 内部调度 ----------------------------------------------------------

    def _is_blocked(
        self,
        task: SchedulerTask[T],
        queued_before: List[_Scheduled],
    ) -> bool:
        return self._conflicts_any(task, self._active) or self._conflicts_any(task, queued_before)

    def _conflicts_any(self, task: SchedulerTask[T], candidates: List[_Scheduled]) -> bool:
        return any(
            ToolAccesses.conflict(task.accesses, c.task.accesses) for c in candidates
        )

    def _start(self, scheduled: _Scheduled) -> None:
        self._active.append(scheduled)
        asyncio.ensure_future(self._run(scheduled))

    async def _run(self, scheduled: _Scheduled) -> None:
        try:
            result = await scheduled.task.run()
            if not scheduled.future.done():
                scheduled.future.set_result(result)
        except Exception as exc:  # noqa: BLE001
            if not scheduled.future.done():
                scheduled.future.set_exception(exc)
        finally:
            self._finish(scheduled)

    def _finish(self, scheduled: _Scheduled) -> None:
        try:
            self._active.remove(scheduled)
        except ValueError:
            pass
        self._start_queued()

    def _start_queued(self) -> None:
        still: List[_Scheduled] = []
        for task in self._queued:
            if self._is_blocked(task.task, still):
                still.append(task)
            else:
                self._start(task)
        self._queued = still


__all__ = ["SchedulerTask", "ToolScheduler"]
