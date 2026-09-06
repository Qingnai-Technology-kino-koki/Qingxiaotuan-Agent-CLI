"""Telemetry client: event tracking, scoped contexts, and sink attachment.

自研实现 (对齐上游线协议; 零依赖)。 Network,
process, and crash-handler concerns are omitted (see ``SKIPPED.md``); the pure
queueing / context-merging logic is preserved.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .sink import EventSink
from .types import TelemetryEvent, TelemetryProperties, is_telemetry_primitive

MAX_QUEUE_SIZE = 1000


@dataclass
class TelemetryContextIds:
    device_id: Optional[str] = None
    session_id: Optional[str] = None


@dataclass
class TelemetryShutdownOptions:
    timeout_ms: Optional[float] = None


class SystemMetricsCollectorHandle(Protocol):
    def stop(self) -> None:
        ...


@dataclass
class _ContextOverrides:
    device_id: bool = False
    session_id: bool = False


@dataclass
class _PendingEvent:
    event_id: str
    device_id: Optional[str]
    session_id: Optional[str]
    event: str
    timestamp: float
    properties: TelemetryProperties
    context_overrides: _ContextOverrides = field(default_factory=_ContextOverrides)


def sanitize_properties(input: TelemetryProperties) -> TelemetryProperties:
    return {k: v for k, v in input.items() if is_telemetry_primitive(v)}


def merge_context(
    base: TelemetryContextIds, patch: TelemetryContextIds
) -> TelemetryContextIds:
    return TelemetryContextIds(
        device_id=patch.device_id if patch.device_id is not None else base.device_id,
        session_id=patch.session_id if patch.session_id is not None else base.session_id,
    )


def _to_telemetry_event(event: _PendingEvent) -> TelemetryEvent:
    return TelemetryEvent(
        event_id=event.event_id,
        device_id=event.device_id,
        session_id=event.session_id,
        event=event.event,
        timestamp=event.timestamp,
        properties=event.properties,
    )


class TelemetryClient:
    def __init__(self) -> None:
        self._queue: list[_PendingEvent] = []
        self._sink: Optional[EventSink] = None
        self._system_metrics_collector: Optional[SystemMetricsCollectorHandle] = None
        self._device_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._disabled = False

    def set_context(self, input: TelemetryContextIds) -> None:
        if input.device_id is not None:
            self._device_id = input.device_id
        if input.session_id is not None:
            self._session_id = input.session_id

    def with_context(self, input: TelemetryContextIds) -> "TelemetryClient":
        return ScopedTelemetryClient(self, input)

    def set_system_metrics_collector(
        self, collector: SystemMetricsCollectorHandle
    ) -> None:
        if self._system_metrics_collector is not None and self._system_metrics_collector is not collector:
            self._system_metrics_collector.stop()
        self._system_metrics_collector = collector

    def attach_sink(self, sink: EventSink) -> None:
        if self._sink is not None and self._sink is not sink:
            self._sink.stop_periodic_flush()
            self._sink.flush_sync()
        self._sink = sink
        for event in self._queue:
            record = _to_telemetry_event(event)
            if record.device_id is None and not event.context_overrides.device_id:
                record.device_id = self._device_id
            if record.session_id is None and not event.context_overrides.session_id:
                record.session_id = self._session_id
            sink.accept(record)
        self._queue = []

    def disable(self) -> None:
        self._disabled = True
        self._queue = []
        if self._system_metrics_collector is not None:
            self._system_metrics_collector.stop()
        self._system_metrics_collector = None
        if self._sink is not None:
            self._sink.stop_periodic_flush()
            self._sink.clear_buffer()
            self._sink = None

    def enable(self) -> None:
        self._disabled = False

    def track(self, event: str, properties: TelemetryProperties | None = None) -> None:
        self.track_with_context(event, properties or {}, TelemetryContextIds())

    def track_with_context(
        self,
        event: str,
        properties: TelemetryProperties,
        context: TelemetryContextIds,
    ) -> None:
        if self._disabled:
            return
        record = _PendingEvent(
            event_id=uuid.uuid4().hex,
            device_id=context.device_id if context.device_id is not None else self._device_id,
            session_id=context.session_id if context.session_id is not None else self._session_id,
            event=event,
            timestamp=time.time(),
            properties=sanitize_properties(properties),
            context_overrides=_ContextOverrides(
                device_id=context.device_id is not None,
                session_id=context.session_id is not None,
            ),
        )
        if self._sink is not None:
            self._sink.accept(_to_telemetry_event(record))
            return
        self._queue.append(record)
        if len(self._queue) > MAX_QUEUE_SIZE:
            self._queue = self._queue[-MAX_QUEUE_SIZE:]

    def get_sink(self) -> Optional[EventSink]:
        return self._sink

    async def flush(self, signal: Any = None) -> None:
        if self._sink is not None:
            await self._sink.flush(signal)

    def flush_sync(self) -> None:
        if self._sink is not None:
            self._sink.flush_sync()

    async def shutdown(self, options: TelemetryShutdownOptions | None = None) -> None:
        options = options or TelemetryShutdownOptions()
        if self._system_metrics_collector is not None:
            self._system_metrics_collector.stop()
        self._system_metrics_collector = None
        sink = self._sink
        if sink is None:
            return
        sink.stop_periodic_flush()
        if options.timeout_ms is None:
            await sink.flush()
            return
        try:
            await asyncio.wait_for(sink.flush(), options.timeout_ms / 1000)
        except (asyncio.TimeoutError, Exception):
            sink.flush_sync()

    def reset_for_tests(self) -> None:
        if self._sink is not None:
            self._sink.stop_periodic_flush()
        if self._system_metrics_collector is not None:
            self._system_metrics_collector.stop()
        self._system_metrics_collector = None
        self._queue = []
        self._sink = None
        self._device_id = None
        self._session_id = None
        self._disabled = False


class ScopedTelemetryClient(TelemetryClient):
    def __init__(self, parent: TelemetryClient, context: TelemetryContextIds) -> None:
        super().__init__()
        self._parent = parent
        self._context = context

    def set_context(self, input: TelemetryContextIds) -> None:  # type: ignore[override]
        self._parent.set_context(input)

    def with_context(self, input: TelemetryContextIds) -> "TelemetryClient":  # type: ignore[override]
        return ScopedTelemetryClient(self._parent, merge_context(self._context, input))

    def set_system_metrics_collector(  # type: ignore[override]
        self, collector: SystemMetricsCollectorHandle
    ) -> None:
        self._parent.set_system_metrics_collector(collector)

    def attach_sink(self, sink: EventSink) -> None:  # type: ignore[override]
        self._parent.attach_sink(sink)

    def disable(self) -> None:  # type: ignore[override]
        self._parent.disable()

    def enable(self) -> None:  # type: ignore[override]
        self._parent.enable()

    def track(self, event: str, properties: TelemetryProperties | None = None) -> None:  # type: ignore[override]
        self._parent.track_with_context(event, properties or {}, self._context)

    def get_sink(self) -> Optional[EventSink]:  # type: ignore[override]
        return self._parent.get_sink()

    def flush_sync(self) -> None:  # type: ignore[override]
        self._parent.flush_sync()

    async def flush(self, signal: Any = None) -> None:  # type: ignore[override]
        await self._parent.flush(signal)

    async def shutdown(self, options: TelemetryShutdownOptions | None = None) -> None:  # type: ignore[override]
        await self._parent.shutdown(options)

    def reset_for_tests(self) -> None:  # type: ignore[override]
        self._parent.reset_for_tests()


_default_client = TelemetryClient()


def get_default_telemetry_client() -> TelemetryClient:
    return _default_client


def set_context(input: TelemetryContextIds) -> None:
    _default_client.set_context(input)


def attach_sink(sink: EventSink) -> None:
    _default_client.attach_sink(sink)


def disable() -> None:
    _default_client.disable()


def enable() -> None:
    _default_client.enable()


def track(event: str, properties: TelemetryProperties | None = None) -> None:
    _default_client.track(event, properties)


def with_context(input: TelemetryContextIds) -> TelemetryClient:
    return _default_client.with_context(input)


def get_sink() -> Optional[EventSink]:
    return _default_client.get_sink()


def flush_sync() -> None:
    _default_client.flush_sync()


async def flush(signal: Any = None) -> None:
    await _default_client.flush(signal)


async def shutdown(options: TelemetryShutdownOptions | None = None) -> None:
    await _default_client.shutdown(options)


def reset_default_telemetry_client_for_tests() -> None:
    _default_client.reset_for_tests()


__all__ = [
    "TelemetryContextIds",
    "TelemetryShutdownOptions",
    "SystemMetricsCollectorHandle",
    "TelemetryClient",
    "ScopedTelemetryClient",
    "sanitize_properties",
    "merge_context",
    "get_default_telemetry_client",
    "set_context",
    "attach_sink",
    "disable",
    "enable",
    "track",
    "with_context",
    "get_sink",
    "flush_sync",
    "flush",
    "shutdown",
    "reset_default_telemetry_client_for_tests",
]
