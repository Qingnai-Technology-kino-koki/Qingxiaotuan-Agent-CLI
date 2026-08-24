"""内核事件总线异常隔离测试 (Task #23)。

确保: 单个 handler 抛异常时, 其他 handler 仍正常收到事件, 且主流程不被中断。
"""
import pytest

from qingxiaotuan.core.kernel import Kernel


def test_emit_isolates_handler_exception():
    k = Kernel()
    received = []

    def good(payload):
        received.append(payload.get("x"))

    def bad(payload):
        raise RuntimeError("boom")

    k.on("evt", good)
    k.on("evt", bad)
    # 不应抛异常
    k.emit("evt", {"x": 1})
    assert received == [1]


def test_emit_all_handlers_run_even_if_multiple_fail():
    k = Kernel()
    got = []

    def a(p):
        got.append("a")

    def b(p):
        raise ValueError("b fail")

    def c(p):
        got.append("c")

    k.on("t", a)
    k.on("t", b)
    k.on("t", c)
    k.emit("t", {})
    assert got == ["a", "c"]


def test_emit_wildcard_isolated():
    k = Kernel()
    got = []

    def wide(p):
        raise RuntimeError("wide boom")

    def normal(p):
        got.append("specific")

    k.on("*", wide)
    k.on("specific", normal)
    k.emit("specific", {"v": 1})
    # 通配符 handler 炸了, 但 specific 仍收到
    assert got == ["specific"]


def test_emit_records_event_even_when_handler_fails():
    k = Kernel()
    k.on("e", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    k.emit("e", {"k": "v"})
    # 事件本身被记录, 不丢失
    assert len(k.events) == 1
    assert k.events[-1].type == "e"
    assert k.events[-1].payload == {"k": "v"}
