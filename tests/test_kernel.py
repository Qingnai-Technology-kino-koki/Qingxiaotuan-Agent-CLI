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
