"""Step 重试恢复 —— 失败的 step 自动重试与恢复。

以可 register_loop_error_handler 的 LoopErrorHandler 形式实现：
- match：错误为可重试的 ChatProviderError（连接/超时/限流/配额）。
- handle：累计该 driver 失败次数，未超 max_attempts_per_step 则退避后重投队列头，
  否则返回失败（step 失败）。

说明：TS 的退避来自 retryBackoffDelays + readRetryAfterMs；本端口简化为
read_retry_after_ms(error) 优先，无则 0（测试零延迟），保留「退避 + 重投头」语义。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..contract import (
    APIConnectionError,
    APIProviderQuotaExhaustedError,
    APIProviderRateLimitError,
    APITimeoutError,
    ChatProviderError,
)
from .config import LoopControl
from .loop import AgentLoopService, LoopErrorContext, LoopErrorHandler


# 可重试的 provider 错误类型
_RETRYABLE_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    APIProviderRateLimitError,
    APIProviderQuotaExhaustedError,
)


def is_retryable_error(error: Any) -> bool:
    """判断一个错误是否值得重试（网络/超时/可瞬态恢复的错误才重试）。"""
    return isinstance(error, _RETRYABLE_ERRORS) or (
        isinstance(error, ChatProviderError) and error.code in {
            "connection_error",
            "timeout",
            "rate_limit",
            "quota_exhausted",
        }
    )


def read_retry_after_ms(error: Any) -> int | None:
    """从错误读取重试等待毫秒（无则 None → 0 延迟）。"""
    retry_after = getattr(error, "retry_after_ms", None)
    if isinstance(retry_after, int):
        return retry_after
    return None


class StepRetryService:
    """Step 重试服务：按失败次数/退避策略调度重试。"""

    def __init__(
        self,
        loop_service: AgentLoopService,
        loop_control: LoopControl | None = None,
    ) -> None:
        self._loop = loop_service
        self._control = loop_control or LoopControl.defaults()
        self._attempts: dict[str, int] = {}
        self._last_driver_id: str | None = None

        self._handler = LoopErrorHandler(
            id="step-retry",
            match=lambda ctx: is_retryable_error(ctx.error),
            handle=self._recover,
        )
        self._disposable = loop_service.register_loop_error_handler(self._handler)
        # 成功完成一个 step 时重置计数
        self._reset_disposable = loop_service.hooks.on_did_finish_step.register(
            "step-retry-reset", self._on_step_finished
        )

    async def _on_step_finished(self, ctx: Any) -> None:
        self._reset_attempts()

    def _reset_attempts(self) -> None:
        self._last_driver_id = None
        self._attempts.clear()

    async def _recover(self, context: LoopErrorContext) -> bool:
        driver = context.failed_driver
        if driver is None or context.step is None:
            return False

        if self._last_driver_id != driver.id:
            self._last_driver_id = driver.id
            self._attempts[driver.id] = 0
        self._attempts[driver.id] = self._attempts[driver.id] + 1

        max_attempts = max(self._control.max_attempts_per_step, 1)
        if self._attempts[driver.id] >= max_attempts:
            self._reset_attempts()
            return False

        error = context.error
        delay_ms = read_retry_after_ms(error) or 0
        if delay_ms > 0 and not getattr(context.signal, "is_set", lambda: False)():
            await asyncio.sleep(delay_ms / 1000.0)

        if getattr(context.signal, "is_set", lambda: False)():
            return False

        context.retry(driver, {"at": "head"})
        return True

    def dispose(self) -> None:
        self._disposable.dispose()
        self._reset_disposable.dispose()
