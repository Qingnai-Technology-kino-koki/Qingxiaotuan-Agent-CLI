"""Telemetry event models and primitive validation.

自研实现 (对齐上游线协议; 零依赖, 纯逻辑)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union

# Mirror of JavaScript's Number.MAX_SAFE_INTEGER.
MAX_TELEMETRY_NUMBER_MAGNITUDE = 2**53 - 1

# A telemetry value may only be a primitive. ``bool`` must be checked before
# ``int`` because bool is a subclass of int in Python.
TelemetryPrimitive = Optional[Union[bool, int, float, str]]
TelemetryProperties = Dict[str, TelemetryPrimitive]
TelemetryContext = Dict[str, TelemetryPrimitive]


@dataclass
class TelemetryEvent:
    """A single telemetry event before enrichment with context."""

    event: str
    timestamp: float
    properties: TelemetryProperties
    event_id: str = ""
    device_id: Optional[str] = None
    session_id: Optional[str] = None


@dataclass(frozen=True)
class EnrichedTelemetryEvent:
    """A telemetry event after the sink attaches runtime context."""

    event: str
    timestamp: float
    properties: TelemetryProperties
    context: TelemetryContext
    event_id: str = ""
    device_id: Optional[str] = None
    session_id: Optional[str] = None


def is_telemetry_number(value: float) -> bool:
    return math.isfinite(value) and abs(value) <= MAX_TELEMETRY_NUMBER_MAGNITUDE


def is_telemetry_primitive(value: Any) -> bool:
    """Return True if ``value`` is safe to serialize as telemetry."""
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return is_telemetry_number(value)
    return False


__all__ = [
    "MAX_TELEMETRY_NUMBER_MAGNITUDE",
    "TelemetryPrimitive",
    "TelemetryProperties",
    "TelemetryContext",
    "TelemetryEvent",
    "EnrichedTelemetryEvent",
    "is_telemetry_number",
    "is_telemetry_primitive",
]
