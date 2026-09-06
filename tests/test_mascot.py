"""青小团吉祥物: ascii / svg 渲染与状态机。"""
from qingxiaotuan.ui.mascot import Mascot, IDLE, THINKING, WORKING, ALERT, DONE, STATES


def test_states_constant():
    assert STATES == (IDLE, THINKING, WORKING, ALERT, DONE)


def test_ascii_renders_per_state():
    for st in STATES:
        m = Mascot(st)
        art = m.ascii(0)
        assert isinstance(art, str)
        # Kimi Code 风格: block art logo (2 行) + moon spinner
        assert len(art.splitlines()) >= 2
        # 包含 Kimi Code block art logo
        assert "▐█▛█▛█▌" in art
        assert "▐█████▌" in art
        # 无高亮: 纯文本, 不含 ANSI 转义
        assert "\033[" not in art


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


def test_logo_returns_block_art():
    m = Mascot()
    l1, l2 = m.logo()
    assert l1 == "▐█▛█▛█▌"
    assert l2 == "▐█████▌"


def test_spinner_returns_moon_frame():
    m = Mascot()
    from qingxiaotuan.ui.mascot import MOON_FRAMES
    for i in range(len(MOON_FRAMES)):
        assert m.spinner(i) == MOON_FRAMES[i]


def test_state_icon_per_state():
    expected = {"idle": "◦", "thinking": "◍", "working": "●", "alert": "⚠", "done": "✓"}
    for st, icon in expected.items():
        m = Mascot(st)
        assert m.state_icon() == icon
        assert m.icon() == icon  # 向后兼容
