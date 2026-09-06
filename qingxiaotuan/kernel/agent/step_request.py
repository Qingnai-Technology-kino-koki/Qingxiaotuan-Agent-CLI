"""Step 请求抽象 —— 一次代理循环里要执行的最小工作单元。

- StepRequest：基类，含 id / mergeable / turn_scoped / admission / state。
- MessageStepRequest：携带一条用户消息，物化时把消息回灌到 loop 的历史。
- ContinuationStepRequest：续转请求（finish_reason==tool_calls 且无 stopTurn 时由
  LoopContinuationService 自动入队），物化时不产生新上下文。

说明：TS 的 admission（newTurn / activeOrNewTurn / activeTurnOnly ...）用于多 turn
调度，本端口把 loop 收敛为「单 turn 内的 step 队列」，admission 仅作为元数据保留，
不做跨 turn 路由（评价：该概念在单轮 Python 端口中不重要，故简化）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from ..contract import Message


@dataclass
class StepRequestOptions:
    mergeable: bool = False
    turn_scoped: bool = True
    admission: str = "activeOrNextTurn"
    kind: Optional[str] = None


class StepRequestState:
    PENDING = "pending"
    MATERIALIZED = "materialized"
    ABORTED = "aborted"


class StepRequest:
    """Step 请求基类（抽象）。"""

    def __init__(self, options: Optional[StepRequestOptions] = None) -> None:
        opts = options or StepRequestOptions()
        self.id: str = uuid.uuid4().hex
        self.mergeable: bool = opts.mergeable
        self.turn_scoped: bool = opts.turn_scoped
        self.admission: str = opts.admission
        self.kind: str = opts.kind or "step"
        self._state: str = StepRequestState.PENDING

    # ---- 子类需实现的属性/方法 ----
    @property
    def state(self) -> str:
        return self._state

    @property
    def aborted(self) -> bool:
        return self._state == StepRequestState.ABORTED

    def abort(self) -> bool:
        if self._state != StepRequestState.PENDING:
            return False
        self._state = StepRequestState.ABORTED
        self.on_settled()
        return True

    def on_will_materialize(self) -> None:
        """物化前钩子（子类可重写）。"""

    def mark_materialized(self) -> None:
        if self._state != StepRequestState.PENDING:
            return
        self._state = StepRequestState.MATERIALIZED
        self.on_settled()

    def resolve_context_messages(self) -> List[Message]:
        """物化时回灌到 loop 历史上下文的消息（子类实现）。"""
        raise NotImplementedError

    def on_settled(self) -> None:
        """abort / materialize 时调用（子类可重写）。"""


class MessageStepRequest(StepRequest):
    """携带一条消息的 step 请求（通常是一轮用户输入）。"""

    def __init__(
        self,
        message: Message,
        options: Optional[StepRequestOptions] = None,
    ) -> None:
        opts = options or StepRequestOptions()
        if opts.kind is None:
            opts.kind = "message"
        super().__init__(opts)
        self._message = message

    @property
    def message(self) -> Message:
        return self._message

    def resolve_context_messages(self) -> List[Message]:
        return [self._message]


class ContinuationStepRequest(StepRequest):
    """续转请求：模型要求继续（tool_calls 且非 stopTurn）。"""

    def __init__(self, options: Optional[StepRequestOptions] = None) -> None:
        opts = options or StepRequestOptions()
        if opts.kind is None:
            opts.kind = "continuation"
        # 续转请求默认是 turn 级、不可合并
        if opts.mergeable is None:
            opts.mergeable = False
        super().__init__(opts)

    def resolve_context_messages(self) -> List[Message]:
        return []
