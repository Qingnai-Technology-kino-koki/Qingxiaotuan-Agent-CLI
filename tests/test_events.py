"""内核事件类型与 emit/on 便捷函数测试。"""

from qingxiaotuan.core.events import EventType, emit_event, on_event
from qingxiaotuan.core.kernel import Kernel


def test_event_type_values():
    """EventType 枚举值应与历史字符串一致。"""
    assert EventType.PLUGIN_REGISTERED.value == "plugin.registered"
    assert EventType.TOOL_EXECUTED.value == "tool.executed"
    assert EventType.MODEL_ROUTED.value == "model.routed"
    assert EventType.LOOP_ITERATION.value == "loop.iteration"
    assert EventType.LOOP_REFLECT.value == "loop.reflect"
    assert EventType.EVENT_OVERFLOW.value == "event.overflow"


def test_emit_event_enum_and_str():
    """emit_event 应同时接受枚举与字符串, 并记录事件。"""
    k = Kernel()
    emit_event(k, EventType.TOOL_EXECUTED, {"name": "run_shell", "status": "ok"})
    emit_event(k, "tool.executed", {"name": "read_file", "status": "ok"})
    assert len(k.events) == 2
    assert k.events[0].type == "tool.executed"
    assert k.events[0].payload["name"] == "run_shell"
    assert k.events[1].payload["name"] == "read_file"


def test_on_event_receives_payload():
    """on_event 订阅后应收到对应事件 payload。"""
    k = Kernel()
    received = []
    on_event(k, EventType.MODEL_SWITCHED, lambda p: received.append(p))
    emit_event(k, EventType.MODEL_SWITCHED, {"provider": "openai", "model": "gpt-4"})
    assert received == [{"provider": "openai", "model": "gpt-4"}]


def test_on_event_string_key_matches_enum():
    """字符串订阅与枚举发射应互通 (向后兼容)。"""
    k = Kernel()
    received = []
    on_event(k, "loop.reflect", lambda p: received.append(p))
    emit_event(k, EventType.LOOP_REFLECT, {"n": 1, "decision": "fix"})
    assert received and received[0]["decision"] == "fix"


def test_handler_exception_isolated():
    """单个 handler 抛异常不应中断其他 handler。"""
    k = Kernel()
    received = []

    def bad(payload):
        raise RuntimeError("boom")

    def good(payload):
        received.append(payload)

    on_event(k, EventType.HOOK_EXECUTED, bad)
    on_event(k, EventType.HOOK_EXECUTED, good)
    emit_event(k, EventType.HOOK_EXECUTED, {"hook": "pre", "status": "ok"})
    assert received == [{"hook": "pre", "status": "ok"}]


def test_wildcard_handler():
    """通配符 * 订阅应收到所有事件 (payload 含 type)。"""
    k = Kernel()
    received = []
    k.on("*", lambda p: received.append(p))
    emit_event(k, EventType.SERVICE_PROVIDED, {"service": "config"})
    assert received and received[0]["type"] == "service.provided"


def test_event_overflow_evicts_oldest():
    """事件历史超限时应截断最旧事件并触发 event.overflow。"""
    k = Kernel()
    overflow = []
    k._on_event_overflow = lambda evicted: overflow.extend(evicted)
    for i in range(2100):
        k.emit("tool.executed", {"i": i})
    assert len(k.events) <= 2000
    assert overflow  # 有被驱逐的事件
    assert overflow[0].payload["i"] == 0  # 最旧事件先被驱逐
