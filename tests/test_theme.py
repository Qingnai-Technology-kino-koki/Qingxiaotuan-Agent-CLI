"""M8: 统一主题模块测试 —— 配色派生、占用条与状态图标。"""

from qingxiaotuan.ui.theme import (
    C, PT_STYLE, MASCOT_ICONS,
    context_bar, context_style,
)


def test_rich_palette_has_all_semantic_keys():
    for key in ("accent", "text", "dim", "muted", "ok", "warn", "err", "box", "primary"):
        assert key in C
        assert isinstance(C[key], str)


def test_pt_style_has_context_classes():
    for key in ("context-ok", "context-warn", "context-err"):
        assert key in PT_STYLE


def test_mascot_icons_cover_all_states():
    for state in ("idle", "thinking", "working", "alert", "done"):
        assert state in MASCOT_ICONS


def test_context_bar_length_and_fill():
    assert len(context_bar(50)) == 20
    assert context_bar(0) == "░" * 20
    assert context_bar(100) == "█" * 20
    assert context_bar(150) == "█" * 20  # 钳制
    assert context_bar(-5) == "░" * 20


def test_context_style_thresholds():
    assert context_style(30) == "ok"
    assert context_style(50) == "warn"
    assert context_style(79) == "warn"
    assert context_style(80) == "err"
    assert context_style(95) == "err"
