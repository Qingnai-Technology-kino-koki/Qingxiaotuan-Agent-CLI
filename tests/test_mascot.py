"""青小团吉祥物: ascii / svg 渲染与状态机。"""
from qingxiaotuan.ui.mascot import Mascot, IDLE, THINKING, WORKING, ALERT, DONE, STATES


def test_states_constant():
    assert STATES == (IDLE, THINKING, WORKING, ALERT, DONE)


def test_ascii_renders_per_state():
    for st in STATES:
        m = Mascot(st)
        art = m.ascii(0)
        assert isinstance(art, str)
        assert len(art.splitlines()) >= 3
        # ANSI 着色存在 (24bit 转义)
        assert "\033[" in art


def test_set_invalid_state_ignored():
    m = Mascot(IDLE)
    m.set("bogus")
    assert m.state == IDLE


def test_tick_cycles():
    m = Mascot(IDLE)
    seen = {m.tick() for _ in range(8)}
    assert len(seen) > 1  # 帧在变化


def test_svg_valid_per_state():
    for st in STATES:
        svg = Mascot(st).svg(100)
        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")
        # 各状态应有对应动画段
        assert "animateTransform" in svg or "animate " in svg


def test_svg_done_has_smile_path():
    svg = Mascot(DONE).svg()
    # done 状态嘴巴是上扬弯月
    assert "Q60 92 70 82" in svg
