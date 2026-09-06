"""Buffered event sink with runtime-context enrichment.

自研实现 (对齐上游线协议; 零依赖)。 The
original built its context from ``node:os`` / ``process``; here we derive an
equivalent context from the Python stdlib ``platform`` / ``os`` modules so the
sink stays free of third-party dependencies.
"""
from __future__ import annotations

import asyncio
import os
import platform as _platform
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .transport import TelemetryTransport
from .types import (
    EnrichedTelemetryEvent,
    TelemetryContext,
    TelemetryEvent,
    TelemetryPrimitive,
)

DEFAULT_FLUSH_INTERVAL_MS = 30_000
DEFAULT_FLUSH_THRESHOLD = 50


@dataclass
class EventSinkContextOptions:
    app_name: str
    version: str
    ui_mode: Optional[str] = None
    model: Optional[str] = None
    build_sha: Optional[str] = None
    terminal: Optional[str] = None
    locale: Optional[str] = None
    env: Optional[Mapping[str, str]] = None


def _set_primitive(
    target: TelemetryContext, key: str, value: TelemetryPrimitive | None
) -> None:
    if value is None:
        return
    if isinstance(value, str) and value == "":
        return
    target[key] = value


def build_context(options: EventSinkContextOptions) -> TelemetryContext:
    env = options.env if options.env is not None else dict(os.environ)
    context: TelemetryContext = {
        "app_name": options.app_name,
        "version": options.version,
        "runtime": "python",
        "platform": _platform.system().lower(),
        "arch": _platform.machine(),
        "python_version": _platform.python_version(),
        "os_version": _platform.release(),
        "ci": "CI" in env,
        "locale": options.locale if options.locale is not None else env.get("LANG", ""),
        "terminal": options.terminal
        if options.terminal is not None
        else env.get("TERM_PROGRAM", ""),
        "ui_mode": options.ui_mode if options.ui_mode is not None else "shell",
    }
    _set_primitive(context, "model", options.model)
    _set_primitive(context, "build_sha", options.build_sha)
    return context


class EventSink:
    def __init__(
        self,
        transport: TelemetryTransport,
        context: EventSinkContextOptions,
        flush_interval_ms: int | None = None,
        flush_threshold: int | None = None,
    ) -> None:
        self.transport = transport
        self.context = build_context(context)
        self.flush_interval_ms = flush_interval_ms or DEFAULT_FLUSH_INTERVAL_MS
        self.flush_threshold = flush_threshold or DEFAULT_FLUSH_THRESHOLD
        self._buffer: list[EnrichedTelemetryEvent] = []
        self._timer: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

    def accept(self, event: TelemetryEvent) -> None:
        enriched = EnrichedTelemetryEvent(
            event_id=event.event_id,
            device_id=event.device_id,
            session_id=event.session_id,
            event=event.event,
            timestamp=event.timestamp,
            properties=dict(event.properties),
            context=dict(self.context),
        )
        self._buffer.append(enriched)
        if len(self._buffer) >= self.flush_threshold:
            self._maybe_auto_flush()

    def start_periodic_flush(self) -> None:
        if self._timer is not None:
            return
        self._stop_event = threading.Event()
        worker = threading.Thread(target=self._periodic_loop, daemon=True)
        worker.start()
        self._timer = worker

    def stop_periodic_flush(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._timer is not None:
            self._timer.join(timeout=1.0)
        self._timer = None
        self._stop_event = None

    def retry_disk_events(self) -> Any:
        return self.transport.retry_disk_events()

    def clear_buffer(self) -> None:
        self._buffer = []

    async def flush(self, signal: Any = None) -> None:
        if len(self._buffer) == 0:
            return
        events = self._buffer
        self._buffer = []
        await self.transport.send(events, signal)

    def flush_sync(self) -> None:
        if len(self._buffer) == 0:
            return
        events = self._buffer
        self._buffer = []
        try:
            self.transport.save_to_disk(events)
        except Exception:
            # Telemetry must never make shutdown fail.
            pass

    def _maybe_auto_flush(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.flush())

    def _periodic_loop(self) -> None:
        assert self._stop_event is not None
        while True:
            if self._stop_event.wait(self.flush_interval_ms / 1000):
                break
            try:
                asyncio.run(self.flush())
            except Exception:
                pass


__all__ = [
    "DEFAULT_FLUSH_INTERVAL_MS",
    "DEFAULT_FLUSH_THRESHOLD",
    "EventSinkContextOptions",
    "build_context",
    "EventSink",
]
