"""M8: 统一主题模块测试 —— 配色派生、占用条与状态图标。"""

from qingxiaotuan.ui.theme import (
    C, PT_STYLE, MASCOT_ICONS,
    blank_pt_style, context_bar, context_style,
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


def test_blank_pt_style_overrides_all_defaults():
    """无高亮: 重置样式表必须覆盖 prompt_toolkit 全部默认样式规则。"""
    blank = blank_pt_style()
    try:
        from prompt_toolkit.styles.defaults import default_ui_style
        from prompt_toolkit.styles import Style, merge_styles
    except Exception:
        return  # prompt_toolkit 不可用时跳过
    for cls, _ in default_ui_style().style_rules:
        assert cls in blank, f"默认样式规则未覆盖: {cls}"
        assert blank[cls] != "", f"默认样式规则未重置: {cls}"
    # 合并后所有关键类必须解析为无颜色/无加粗 (继承机制下空串无效, 需显式重置)。
    merged = merge_styles([default_ui_style(), Style.from_dict(blank)])
    for cls in ("completion-menu.completion", "completion-menu", "scrollbar.background",
                "search-toolbar", "selected", "bottom-toolbar", "line-number",
                "auto-suggestion", "dialog", "button.focused"):
        attrs = merged.get_attrs_for_style_str("class:" + cls)
        assert attrs.color in (None, "default"), f"{cls} 仍有前景色: {attrs.color}"
        assert attrs.bgcolor in (None, "default"), f"{cls} 仍有背景色: {attrs.bgcolor}"
        assert not attrs.bold, f"{cls} 仍加粗"


def test_blank_pt_style_keeps_custom_keys():
    blank = blank_pt_style()
    for key in ("status", "title", "panel", "input", "bottom-toolbar"):
        assert key in blank
        assert blank[key] != ""
