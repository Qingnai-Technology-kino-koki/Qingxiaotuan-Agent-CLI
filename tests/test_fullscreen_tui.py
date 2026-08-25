from qingxiaotuan.ui.fullscreen import FullScreenTUI


def test_fullscreen_tui_renders_mascot_states():
    tui = FullScreenTUI(lambda _text: "ok")
    for state, marker in (("idle", "◡"), ("thinking", "◠"), ("working", "•"),
                          ("alert", "⚠"), ("done", "✦")):
        tui.set_mascot(state)
        rendered = "".join(text for _, text in tui._render_side())
        assert marker in rendered


def test_fullscreen_tui_close_is_idempotent():
    tui = FullScreenTUI(lambda _text: "ok")
    tui.close()
    tui.close()
    assert tui._closed is True