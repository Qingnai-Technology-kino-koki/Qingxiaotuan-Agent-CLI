"""自研实现 (对齐上游 telemetry 协议; Python 3.11 惯用写法, 归属见 NOTICE)。

Only the pure, node-independent logic is reproduced (event models, client
queueing/scoped contexts, buffered sink with context enrichment, payload
serialization, and remote-URL normalization). Network, filesystem, process,
and crash-handler modules are intentionally skipped — see ``SKIPPED.md``.
"""
from __future__ import annotations

from .client import (
    ScopedTelemetryClient,
    TelemetryClient,
    TelemetryContextIds,
    TelemetryShutdownOptions,
    attach_sink,
    disable,
    enable,
    flush,
    flush_sync,
    get_default_telemetry_client,
    get_sink,
    merge_context,
    reset_default_telemetry_client_for_tests,
    sanitize_properties,
    set_context,
    shutdown,
    track,
    with_context,
)
from .remote import normalize_remote
from .sink import (
    DEFAULT_FLUSH_INTERVAL_MS,
    DEFAULT_FLUSH_THRESHOLD,
    EventSink,
    EventSinkContextOptions,
    build_context,
)
from .transport import (
    SERVER_EVENT_PREFIX,
    TELEMETRY_ENDPOINT,
    USER_ID_PREFIX,
    RETRY_BACKOFFS_MS,
    TransientTelemetryError,
    TelemetryTransport,
    apply_server_prefix,
    build_payload,
    build_user_id,
    flatten_event,
    handle_status,
)
from .types import (
    EnrichedTelemetryEvent,
    MAX_TELEMETRY_NUMBER_MAGNITUDE,
    TelemetryContext,
    TelemetryEvent,
    TelemetryPrimitive,
    TelemetryProperties,
    is_telemetry_number,
    is_telemetry_primitive,
)

__all__ = [
    # client
    "TelemetryClient",
    "ScopedTelemetryClient",
    "TelemetryContextIds",
    "TelemetryShutdownOptions",
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
    # remote
    "normalize_remote",
    # sink
    "DEFAULT_FLUSH_INTERVAL_MS",
    "DEFAULT_FLUSH_THRESHOLD",
    "EventSink",
    "EventSinkContextOptions",
    "build_context",
    # transport
    "SERVER_EVENT_PREFIX",
    "USER_ID_PREFIX",
    "TELEMETRY_ENDPOINT",
    "RETRY_BACKOFFS_MS",
    "TransientTelemetryError",
    "TelemetryTransport",
    "apply_server_prefix",
    "build_payload",
    "build_user_id",
    "flatten_event",
    "handle_status",
    # types
    "EnrichedTelemetryEvent",
    "MAX_TELEMETRY_NUMBER_MAGNITUDE",
    "TelemetryContext",
    "TelemetryEvent",
    "TelemetryPrimitive",
    "TelemetryProperties",
    "is_telemetry_number",
    "is_telemetry_primitive",
]
