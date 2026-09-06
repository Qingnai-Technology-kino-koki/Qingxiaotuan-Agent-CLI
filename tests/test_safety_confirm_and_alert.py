"""多阶段确认 (真实令牌 + 屏幕位置变化) 与安全事件总线实时告警测试。

对应实现:
- qingxiaotuan/core/whitelist.py: MultiStageConfirm / interactive_confirm / make_confirm_token
- qingxiaotuan/core/security_bus.py: SecurityEventBus.set_alerter + emit 实时告警

这些是把"不同位置多阶段确认""实时告警"从文档宣称落地为真实机制的回归测试。
"""
import sys
from unittest import mock

from qingxiaotuan.core.whitelist import (
    MultiStageConfirm,
    make_confirm_token,
    interactive_confirm,
)
from qingxiaotuan.core.security_bus import (
    SecurityEvent,
    SecurityEventBus,
    SecurityEventType,
)


# -------------------------------------------------- 令牌生成
def test_make_confirm_token_format():
    tok = make_confirm_token()
    assert len(tok) == 4
    assert tok.isalnum()


# -------------------------------------------------- 多阶段确认: 每阶段一次性令牌
def test_multistage_requires_per_stage_token():
    """每阶段带 expected; 正确码通过, 且 5 次都有独立令牌。"""
    seen = []

    def fn(msg, *, expected=None, position=None):
        seen.append((expected, position))
        # 模拟用户正确键入每步的独立令牌
        return expected is not None

    c = MultiStageConfirm(confirm_fn=fn, min_interval=0)
    assert c.confirm("dd if=/dev/zero of=/dev/nvme0n1", 5, "极危") is True
    assert len(seen) == 5
    assert all(e for e, _ in seen)


def test_multistage_wrong_token_rejected():
    """用户键错确认码 (或回车) -> 拒绝执行。"""

    def fn(msg, *, expected=None, position=None):
        return False

    c = MultiStageConfirm(confirm_fn=fn, min_interval=0)
    assert c.confirm("rm -rf /", 3, "高危") is False


def test_multistage_positions_vary():
    """5 次确认不应全在同一屏幕位置 (随机化防自动点击固定坐标)。"""
    positions = []

    def fn(msg, *, expected=None, position=None):
        positions.append(position)
        return True

    c = MultiStageConfirm(confirm_fn=fn, min_interval=0)
    c.confirm("cmd", 5, "x")
    assert len(set(positions)) >= 2


def test_multistage_legacy_lambda_fallback():
    """不支持 expected/position 关键字的回调 (如非交互式 lambda) 退化为纯 yes/no。"""
    c = MultiStageConfirm(confirm_fn=lambda m: True, min_interval=0)
    assert c.confirm("cmd", 3, "x") is True
    c2 = MultiStageConfirm(confirm_fn=lambda m: False, min_interval=0)
    assert c2.confirm("cmd", 3, "x") is False


def test_multistage_no_confirm_non_tty_fail_closed():
    """无确认回调且非交互终端 -> fail-closed (无人值守默认拒绝高危命令)。"""
    with mock.patch.object(sys, "stdin", mock.MagicMock(isatty=lambda: False)):
        c = MultiStageConfirm(confirm_fn=None, min_interval=0)
        assert c.confirm("rm -rf /", 5, "x") is False


# -------------------------------------------------- 交互式确认 (终端兜底)
def test_interactive_confirm_token_match():
    with mock.patch("builtins.input", return_value="abcd"):
        assert interactive_confirm("prompt", expected="ABCD", position=0) is True
    with mock.patch("builtins.input", return_value="wrong"):
        assert interactive_confirm("prompt", expected="ABCD", position=0) is False
    with mock.patch("builtins.input", return_value=""):
        assert interactive_confirm("prompt", expected="ABCD", position=0) is False


def test_interactive_confirm_yes_no():
    with mock.patch("builtins.input", return_value="y"):
        assert interactive_confirm("prompt") is True
    with mock.patch("builtins.input", return_value="n"):
        assert interactive_confirm("prompt") is False


def test_interactive_confirm_position_offsets():
    """position 控制横幅前空行数, 使每次确认出现在屏幕不同垂直位置。"""
    with mock.patch("builtins.input", return_value="y"):
        # 不同 position 不应抛异常, 且都被接受 (纯 yes/no 路径)
        for pos in range(5):
            assert interactive_confirm("prompt", position=pos) is True


# -------------------------------------------------- 安全总线实时告警
def test_security_bus_alerter_on_critical_block():
    bus = SecurityEventBus()
    fired = []
    bus.set_alerter(lambda e: fired.append(e))
    bus.emit(SecurityEvent(
        SecurityEventType.COMMAND_BLOCKED, 1.0,
        {"command": "rm -rf /", "reason": "hard_redline"}, severity="high",
    ))
    bus.emit(SecurityEvent("security.summary", 2.0, {}, severity="info"))
    assert len(fired) == 1
    assert fired[0].event_type == SecurityEventType.COMMAND_BLOCKED


def test_security_bus_alerter_critical_event():
    bus = SecurityEventBus()
    fired = []
    bus.set_alerter(lambda e: fired.append(e))
    bus.emit(SecurityEvent(
        SecurityEventType.HARD_REDLINE_HIT, 1.0,
        {"command": "dd if=/dev/zero of=/dev/nvme0n1"}, severity="critical",
    ))
    assert len(fired) == 1


def test_security_bus_no_alerter_is_noop():
    """未设置 alerter 时 emit 不应抛异常。"""
    bus = SecurityEventBus()
    bus.emit(SecurityEvent(
        SecurityEventType.COMMAND_BLOCKED, 1.0, {"command": "x"}, severity="high",
    ))
    assert bus.get_stats()["total_events"] == 1


def test_security_bus_alerter_exception_isolated():
    """alerter 自身抛异常不得影响事件总线。"""
    bus = SecurityEventBus()
    bus.set_alerter(lambda e: 1 / 0)
    bus.emit(SecurityEvent(
        SecurityEventType.COMMAND_BLOCKED, 1.0, {"command": "x"}, severity="high",
    ))
    assert bus.get_stats()["total_events"] == 1
