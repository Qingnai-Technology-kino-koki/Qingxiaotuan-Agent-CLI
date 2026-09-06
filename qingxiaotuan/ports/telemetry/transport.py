"""Telemetry payload serialization.

自研实现 (node 无关的纯逻辑辅助, 对齐上游线协议; 零依赖。
the upstream TypeScript reference implementation. The network/disk
``AsyncTransport`` class is intentionally skipped (see ``SKIPPED.md``); only the
pure serialization logic is reproduced here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .types import (
    EnrichedTelemetryEvent,
    TelemetryPrimitive,
    is_telemetry_primitive,
)

SERVER_EVENT_PREFIX = "kfc_"
USER_ID_PREFIX = "kfc_device_id_"

# Exposed for callers that want the cn-default endpoint without depending on
# the network transport. Mirrors TELEMETRY_ENDPOINT in the TS source.
TELEMETRY_ENDPOINT = "https://telemetry-logs.kimi.com/v1/event"
RETRY_BACKOFFS_MS: tuple[int, ...] = (1_000, 4_000, 16_000)


class TransientTelemetryError(Exception):
    """Raised when a send should be retried (5xx or 429)."""


class TelemetryTransport(ABC):
    """Sink abstraction: deliver enriched events or persist them to disk."""

    @abstractmethod
    async def send(
        self,
        events: Sequence[EnrichedTelemetryEvent],
        signal: Any = None,
    ) -> None:
        ...

    @abstractmethod
    def save_to_disk(self, events: Sequence[EnrichedTelemetryEvent]) -> None:
        ...

    @abstractmethod
    async def retry_disk_events(self) -> None:
        ...


def build_user_id(device_id: str) -> str:
    return USER_ID_PREFIX + device_id


def apply_server_prefix(event: EnrichedTelemetryEvent) -> EnrichedTelemetryEvent:
    if not event.event.startswith(SERVER_EVENT_PREFIX):
        return _replace_event(event, SERVER_EVENT_PREFIX + event.event)
    return event


def flatten_event(event: EnrichedTelemetryEvent) -> dict[str, TelemetryPrimitive]:
    out: dict[str, TelemetryPrimitive] = {}
    source = asdict(event)
    for key, value in source.items():
        if key == "properties":
            _flatten_nested(out, "property", value)
        elif key == "context":
            _flatten_nested(out, "context", value)
        else:
            _assert_primitive(key, value)
            out[key] = value
    return out


def build_payload(
    events: Sequence[EnrichedTelemetryEvent],
    device_id: str,
) -> dict[str, object]:
    return {
        "user_id": build_user_id(device_id),
        "events": [flatten_event(apply_server_prefix(e)) for e in events],
    }


def handle_status(status: int) -> None:
    """Mirror transport.ts ``handleStatus``: 5xx/429 are transient."""
    if status >= 500 or status == 429:
        raise TransientTelemetryError(f"HTTP {status}")
    # Non-4xx and other 4xx are terminal successes / client errors: ignore.


def _replace_event(event: EnrichedTelemetryEvent, new_name: str) -> EnrichedTelemetryEvent:
    # dataclass is frozen; reconstruct with the new event name.
    return EnrichedTelemetryEvent(
        event_id=event.event_id,
        device_id=event.device_id,
        session_id=event.session_id,
        event=new_name,
        timestamp=event.timestamp,
        properties=event.properties,
        context=event.context,
    )


def _flatten_nested(
    target: dict[str, TelemetryPrimitive],
    prefix: str,
    value: object,
) -> None:
    if value is None or not isinstance(value, Mapping):
        return
    for key, nested in value.items():
        _assert_primitive(f"{prefix}.{key}", nested)
        target[f"{prefix}_{key}"] = nested  # type: ignore[index]


def _assert_primitive(key: str, value: object) -> None:
    if is_telemetry_primitive(value):
        return
    raise TypeError(f"telemetry {key} must be primitive")


__all__ = [
    "SERVER_EVENT_PREFIX",
    "USER_ID_PREFIX",
    "TELEMETRY_ENDPOINT",
    "RETRY_BACKOFFS_MS",
    "TransientTelemetryError",
    "build_user_id",
    "apply_server_prefix",
    "flatten_event",
    "build_payload",
    "handle_status",
]
