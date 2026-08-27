"""微内核测试: 注册、依赖拓扑激活、服务发现、事件流。"""

import pytest

from qingxiaotuan.core.kernel import Kernel, Plugin, PluginError


class P(Plugin):
    def __init__(self, name, provides=None, requires=None):
        super().__init__(name=name, provides=provides or [], requires=requires or [])

    def activate(self, kernel):
        for svc in self.provides:
            kernel.provide(svc, f"impl-{svc}", owner=self.name)


def test_dependency_order_activation():
    k = Kernel()
    k.register(P("b", provides=["svc_b"], requires=["svc_a"]))  # 先注册但依赖后者
    k.register(P("a", provides=["svc_a"]))
    k.activate_all()
    assert k.require("svc_a") == "impl-svc_a"
    assert k.require("svc_b") == "impl-svc_b"


def test_missing_dependency_raises():
    k = Kernel()
    k.register(P("x", requires=["nonexistent"]))
    with pytest.raises(PluginError):
        k.activate_all()


def test_duplicate_service_rejected():
    k = Kernel()
    k.register(P("a", provides=["s"]))
    k.register(P("b", provides=["s"]))
    with pytest.raises(PluginError):
        k.activate_all()


def test_event_stream_is_append_only():
    k = Kernel()
    seen = []
    k.on("*", lambda e: seen.append(e["type"]))
    k.register(P("a", provides=["s"]))
    k.activate_all()
    assert "plugin.registered" in seen
    assert "plugin.activated" in seen
    assert len(k.events) == len({e.seq for e in k.events})  # seq 单调唯一


def test_event_overflow_callback():
    """事件历史上限时, on_event_overflow 被调用且收到被丢弃的事件列表。"""
    from qingxiaotuan.core.kernel import _MAX_EVENTS
    evicted_log: list = []
    k = Kernel(on_event_overflow=lambda evicted: evicted_log.extend(evicted))
    # 发射 _MAX_EVENTS + 5 条事件, 触发截断
    for i in range(_MAX_EVENTS + 5):
        k.emit("test.overflow", {"i": i})
    assert len(k.events) <= _MAX_EVENTS
    assert len(evicted_log) == 5
    # 被丢弃的事件 seq 应是最早的
    assert evicted_log[0].seq == 1
    assert evicted_log[-1].seq == 5


def test_event_overflow_emits_notification():
    """事件溢出时自动发射 event.overflow 事件 (每次截断触发一次)。"""
    from qingxiaotuan.core.kernel import _MAX_EVENTS
    overflow_events: list = []
    k = Kernel()
    k.on("event.overflow", lambda e: overflow_events.append(e))
    for i in range(_MAX_EVENTS + 3):
        k.emit("test.ping", {"i": i})
    # 每次 emit 超出上限都触发一次溢出通知 (增量截断)
    assert len(overflow_events) >= 1
    last = overflow_events[-1]
    assert last["evicted"] >= 1
    assert last["remaining"] <= _MAX_EVENTS
