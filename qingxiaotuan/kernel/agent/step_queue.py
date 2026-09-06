"""Step 请求队列 —— 按批次排队并调度 StepRequest。

批处理语义（take_next_batch）：
- 选 driver：队列中第一个「不可合并」(mergeable=False) 的请求；若全部可合并则取第 0 个。
- merged：余下所有「可合并」的请求合并进同一批。
- 其余「不可合并」请求留待下一批。
- 每次 take 会把已消费（driver + merged）从队列移除。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .step_request import StepRequest


@dataclass
class StepRequestBatch:
    driver: StepRequest
    merged: List[StepRequest]


class StepRequestQueue:
    def __init__(self) -> None:
        self._items: List[StepRequest] = []

    def enqueue(self, request: StepRequest, at: str = "tail") -> None:
        if at == "head":
            self._items.insert(0, request)
        else:
            self._items.append(request)

    def has_pending_requests(self) -> bool:
        return any(not item.aborted for item in self._items)

    def take_next_batch(self) -> StepRequestBatch | None:
        self._discard_aborted()
        if not self._items:
            return None

        driver_index = next((i for i, it in enumerate(self._items) if not it.mergeable), 0)
        driver = self._items[driver_index]

        merged: List[StepRequest] = []
        rest: List[StepRequest] = []
        for i, item in enumerate(self._items):
            if i == driver_index:
                continue
            if item.mergeable:
                merged.append(item)
            else:
                rest.append(item)

        self._items = rest
        return StepRequestBatch(driver=driver, merged=merged)

    def drain(self) -> List[StepRequest]:
        return self._items

    def abort_turn_scoped(self) -> None:
        for item in self._items:
            if item.turn_scoped:
                item.abort()
        self._discard_aborted()

    def _discard_aborted(self) -> None:
        self._items = [it for it in self._items if not it.aborted]
