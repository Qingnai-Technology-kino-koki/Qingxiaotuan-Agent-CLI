"""Tests for qingxiaotuan.ports.telemetry (kimi-code telemetry port)."""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from dataclasses import asdict

import pytest

# Some sandboxes pre-seed ``tempfile.tempdir`` to a shared system %TEMP% that
# contains a stale, permission-locked ``pytest-current`` junction, which makes
# pytest's session-finish temp cleanup raise PermissionError and fail the run
# (this affects the whole repo's suite, not just this file). Redirect pytest's
# temp root to a writable, project-local directory so the suite can finish
# cleanly. Harmless in a normal environment.
_TEMP_ROOT = os.path.join(os.path.dirname(__file__), "..", ".pytest-tmp")
os.makedirs(_TEMP_ROOT, exist_ok=True)
# Save original tempdir before changing it to avoid test pollution.
_original_tempdir = tempfile.tempdir
tempfile.tempdir = os.path.abspath(_TEMP_ROOT)


# The import-time override only needs to be active while pytest resolves its
# session basetemp (project-local, avoids a stale permission-locked junction in
# the shared %TEMP%). After that it would silently hijack every other test
# module's NamedTemporaryFile calls (N.B. paths here contain spaces, which
# breaks path-splitting logic in the shell TOCTOU guard). Restore it once this
# module's own tests finish.
@pytest.fixture(scope="module", autouse=True)
def _restore_global_tempdir():
    yield
    tempfile.tempdir = _original_tempdir

from qingxiaotuan.ports.telemetry import (
    EnrichedTelemetryEvent,
    EventSink,
    EventSinkContextOptions,
    ScopedTelemetryClient,
    TelemetryClient,
    TelemetryContextIds,
    TelemetryTransport,
    TransientTelemetryError,
    apply_server_prefix,
    build_payload,
    build_user_id,
    flatten_event,
    handle_status,
    is_telemetry_primitive,
    normalize_remote,
    sanitize_properties,
    track,
)
from qingxiaotuan.ports.telemetry.types import TelemetryEvent


class InMemoryTransport(TelemetryTransport):
    def __init__(self) -> None:
        self.sent: list[EnrichedTelemetryEvent] = []
        self.disk: list[EnrichedTelemetryEvent] = []

    async def send(self, events, signal=None):
        self.sent.extend(events)

    def save_to_disk(self, events):
        self.disk.extend(events)

    async def retry_disk_events(self):
        return None


def _context_options() -> EventSinkContextOptions:
    return EventSinkContextOptions(app_name="qxt", version="1.0.0")


# --------------------------------------------------------------------------
# Primitive validation
# --------------------------------------------------------------------------
def test_is_telemetry_primitive():
    assert is_telemetry_primitive(None)
    assert is_telemetry_primitive(True)
    assert is_telemetry_primitive("x")
    assert is_telemetry_primitive(1)
    assert is_telemetry_primitive(1.5)
    assert not is_telemetry_primitive([1, 2])
    assert not is_telemetry_primitive({"a": 1})
    assert not is_telemetry_primitive(float("inf"))
    assert not is_telemetry_primitive(float("nan"))


def test_is_telemetry_primitive_magnitude():
    big = 2.0**53  # Number.MAX_SAFE_INTEGER + 1
    assert not is_telemetry_primitive(big)


def test_sanitize_properties_drops_non_primitives():
    out = sanitize_properties({"a": 1, "b": "x", "c": [1], "d": {"k": 1}, "e": None, "f": True})
    assert out == {"a": 1, "b": "x", "e": None, "f": True}


# --------------------------------------------------------------------------
# Client -> sink -> transport capture
# --------------------------------------------------------------------------
def test_client_emits_to_sink():
    client = TelemetryClient()
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    client.attach_sink(sink)
    client.set_context(TelemetryContextIds(device_id="dev", session_id="sess"))
    client.track("my_event", {"k": "v", "bad": [1, 2, 3]})

    asyncio.run(sink.flush())

    assert len(transport.sent) == 1
    event = transport.sent[0]
    assert event.event == "my_event"
    assert event.properties == {"k": "v"}  # bad list was sanitized
    assert event.device_id == "dev"
    assert event.session_id == "sess"
    assert event.event_id  # generated uuid hex
    assert event.context["app_name"] == "qxt"
    assert event.context["runtime"] == "python"


def test_queue_before_sink_then_emit():
    client = TelemetryClient()
    client.track("queued")  # no sink yet -> buffered in client queue

    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    client.attach_sink(sink)
    asyncio.run(sink.flush())

    assert len(transport.sent) == 1
    assert transport.sent[0].event == "queued"


def test_device_session_filled_on_attach():
    client = TelemetryClient()
    client.set_context(TelemetryContextIds(device_id="d1", session_id="s1"))
    client.track("pre")  # queued without explicit ids
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    client.attach_sink(sink)
    asyncio.run(sink.flush())
    assert transport.sent[0].device_id == "d1"
    assert transport.sent[0].session_id == "s1"


def test_disabled_client_drops_events():
    client = TelemetryClient()
    client.disable()
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    client.attach_sink(sink)
    client.track("dropped")
    asyncio.run(sink.flush())
    assert len(transport.sent) == 0


# --------------------------------------------------------------------------
# Scoped context (with_context)
# --------------------------------------------------------------------------
def test_with_context_override():
    client = TelemetryClient()
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    client.attach_sink(sink)
    client.set_context(TelemetryContextIds(session_id="base"))

    scoped: TelemetryClient = client.with_context(TelemetryContextIds(device_id="d1"))
    scoped.track("e")
    asyncio.run(sink.flush())

    event = transport.sent[0]
    assert event.device_id == "d1"
    assert event.session_id == "base"  # inherited from parent


def test_scoped_override_is_not_global():
    client = TelemetryClient()
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    client.attach_sink(sink)
    client.set_context(TelemetryContextIds(device_id="global"))

    scoped = client.with_context(TelemetryContextIds(device_id="scoped"))
    scoped.track("s")
    client.track("g")
    asyncio.run(sink.flush())

    by_event = {e.event: e for e in transport.sent}
    assert by_event["s"].device_id == "scoped"
    assert by_event["g"].device_id == "global"


def test_nested_scopes_merge():
    client = TelemetryClient()
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    client.attach_sink(sink)
    outer = client.with_context(TelemetryContextIds(device_id="d1"))
    inner = outer.with_context(TelemetryContextIds(session_id="s2"))
    inner.track("deep")
    asyncio.run(sink.flush())
    event = transport.sent[0]
    assert event.device_id == "d1"
    assert event.session_id == "s2"


# --------------------------------------------------------------------------
# Sink buffering
# --------------------------------------------------------------------------
def test_flush_sync_persists_to_disk():
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    sink.accept(
        TelemetryEvent(event="x", timestamp=1.0, properties={"a": 1})
    )
    sink.flush_sync()
    assert len(transport.disk) == 1
    assert len(sink._buffer) == 0


def test_flush_empty_is_noop():
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    asyncio.run(sink.flush())
    assert transport.sent == []


def test_periodic_flush_emits():
    transport = InMemoryTransport()
    sink = EventSink(
        transport=transport, context=_context_options(), flush_interval_ms=50
    )
    sink.accept(TelemetryEvent(event="tick", timestamp=1.0, properties={}))
    sink.start_periodic_flush()
    time.sleep(0.3)
    sink.stop_periodic_flush()
    assert len(transport.sent) >= 1


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------
def test_build_payload_and_flatten():
    events = [
        EnrichedTelemetryEvent(
            event_id="e1",
            device_id="d",
            session_id="s",
            event="ping",
            timestamp=1.5,
            properties={"a": "b", "n": 2},
            context={"app": "qxt", "ui_mode": "shell"},
        )
    ]
    payload = build_payload(events, "dev")
    assert payload["user_id"] == build_user_id("dev") == "kfc_device_id_dev"
    flat = payload["events"][0]
    assert flat["event"] == "kfc_ping"  # server prefix applied
    assert flat["property_a"] == "b"
    assert flat["property_n"] == 2
    assert flat["context_app"] == "qxt"
    assert flat["context_ui_mode"] == "shell"
    assert flat["device_id"] == "d"
    assert flat["timestamp"] == 1.5


def test_apply_server_prefix_idempotent():
    ev = EnrichedTelemetryEvent(
        event="kfc_ping", timestamp=0.0, properties={}, context={}
    )
    assert apply_server_prefix(ev).event == "kfc_ping"


def test_flatten_rejects_non_primitive():
    bad = EnrichedTelemetryEvent(
        event="x", timestamp=0.0, properties={"bad": [1]}, context={}
    )
    with pytest.raises(TypeError):
        flatten_event(bad)


def test_handle_status_transient():
    with pytest.raises(TransientTelemetryError):
        handle_status(500)
    with pytest.raises(TransientTelemetryError):
        handle_status(429)
    handle_status(200)  # no raise
    handle_status(400)  # no raise


def test_event_round_trip():
    event = EnrichedTelemetryEvent(
        event_id="abc",
        device_id="d",
        session_id="s",
        event="ping",
        timestamp=123.0,
        properties={"a": 1, "b": "x"},
        context={"app": "qxt"},
    )
    restored = EnrichedTelemetryEvent(**asdict(event))
    assert restored == event


# --------------------------------------------------------------------------
# Remote normalization
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("git@github.com:org/repo.git", "github.com/org/repo"),
        ("https://github.com/org/repo.git", "github.com/org/repo"),
        ("https://github.com/org/repo", "github.com/org/repo"),
        ("  https://gitlab.com/a/b.git  ", "gitlab.com/a/b"),
        ("ssh://git@github.com/org/repo.git", "github.com/org/repo"),
        ("org/repo", "org/repo"),
    ],
)
def test_normalize_remote(raw, expected):
    assert normalize_remote(raw) == expected


# --------------------------------------------------------------------------
# Default client module-level API
# --------------------------------------------------------------------------
def test_default_client_module_api():
    from qingxiaotuan.ports.telemetry import (
        attach_sink,
        get_default_telemetry_client,
        reset_default_telemetry_client_for_tests,
    )

    client = get_default_telemetry_client()
    transport = InMemoryTransport()
    sink = EventSink(transport=transport, context=_context_options())
    attach_sink(sink)
    track("module_event", {"x": 1})
    asyncio.run(sink.flush())
    assert any(e.event == "module_event" for e in transport.sent)
    reset_default_telemetry_client_for_tests()
