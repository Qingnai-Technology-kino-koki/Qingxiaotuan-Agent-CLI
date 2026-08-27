"""M8: 全屏 TUI 测试 —— 渲染、状态管理、面板切换、日志上限。

不进入 prompt_toolkit 事件循环, 直接驱动 FullScreenTUI 的渲染方法与状态机,
验证: 吉祥物状态、状态栏 token/context 面板、Tab 焦点循环、日志追加与上限、
退出无残留线程 (M2 验收)。
"""

import threading

from prompt_toolkit.layout.processors import BeforeInput

from qingxiaotuan.ui.fullscreen import FullScreenTUI


def _tui():
    return FullScreenTUI(lambda _text: "ok")


def _prompt_text(tui) -> str:
    """读取输入框当前提示符 (TextArea 的 prompt 存于 BeforeInput 处理器)。"""
    for p in tui.input.control.input_processors:
        if isinstance(p, BeforeInput):
            return p.text
    return ""


def test_fullscreen_tui_close_leaves_no_threads():
    """M2 验收: 退出后无残留监听器/线程 (daemon 动画线程不阻塞进程退出)。

    只断言 TUI 不新增线程; 其他组件 (如 IPC 引擎) 线程的退出/回收不影响本验收。
    """
    before = {t.name for t in threading.enumerate()}
    tui = _tui()
    tui.close()
    after = {t.name for t in threading.enumerate()}
    assert after - before == set()


def test_fullscreen_tui_renders_mascot_states():
    tui = _tui()
    for state, marker in (("idle", "◡"), ("thinking", "◠"), ("working", "•"),
                          ("alert", "⚠"), ("done", "✦")):
        tui.set_mascot(state)
        rendered = "".join(text for _, text in tui._render_side())
        assert marker in rendered


def test_fullscreen_tui_close_is_idempotent():
    tui = _tui()
    tui.close()
    tui.close()
    assert tui._closed is True


def test_status_bar_shows_tokens_and_context():
    tui = _tui()
    tui.add_tokens(1234)
    tui.set_context_pct(62.5)
    text = "".join(t for _, t in tui._render_status())
    assert "tok=1234" in text
    assert "ctx=62%" in text
    assert "青小团" in text  # 标题


def test_status_bar_context_clamped_to_0_100():
    tui = _tui()
    tui.set_context_pct(-5)
    assert tui._context_pct == 0.0
    tui.set_context_pct(150)
    assert tui._context_pct == 100.0


def test_add_tokens_ignores_negative():
    tui = _tui()
    tui.add_tokens(-100)
    assert tui._tokens == 0
    tui.add_tokens(50)
    assert tui._tokens == 50


def test_append_log_caps_at_500():
    tui = _tui()
    for i in range(600):
        tui.append_log(f"事件 {i}")
    assert len(tui._events) == 500
    assert tui._events[-1] == "事件 599"
    assert "事件 0" not in tui._events


def test_focus_cycles_through_three_panels():
    tui = _tui()
    assert tui._focus_index == 0
    for expected in (1, 2, 0):
        tui._cycle_focus()
        assert tui._focus_index == expected
    # 焦点变化会写入日志
    assert any("[焦点]" in e for e in tui._events)


def test_set_mascot_ignores_unknown_state():
    tui = _tui()
    tui.set_mascot("bogus")
    assert tui._mascot_state == "idle"


def test_events_render_placeholder_when_empty():
    tui = _tui()
    assert "等待任务输入" in tui._render_events()
    tui.append_log("你好")
    assert "你好" in tui._render_events()


def test_context_panel_renders_bar_and_tokens():
    tui = _tui()
    tui.set_context_info(6200, 10000)
    side = "".join(text for _, text in tui._render_side())
    assert "上下文 62%" in side
    assert "█" in side and "░" in side
    assert "tok 6,200 / 10,000" in side
    # 状态栏同步显示 ctx 百分比
    status = "".join(t for _, t in tui._render_status())
    assert "ctx=62%" in status


def test_context_bar_method_matches_repl_signature():
    tui = _tui()
    tui.context_bar({"estimated_tokens": 8000, "budget_tokens": 10000})
    assert tui._context_pct == 80.0
    side = "".join(text for _, text in tui._render_side())
    assert "上下文 80%" in side


def test_context_panel_hidden_until_info_set():
    tui = _tui()
    side = "".join(text for _, text in tui._render_side())
    assert "上下文" not in side


def test_mode_prompt_switches_with_mode():
    """Kimi Code 风格: 输入框提示符随模式变化 (Agent ✨ / Plan 📋)。"""
    tui = _tui()
    assert _prompt_text(tui) == "✨ > "
    tui.set_mode("plan")
    assert _prompt_text(tui) == "📋 > "
    tui.set_mode("agent")
    assert _prompt_text(tui) == "✨ > "
    tui.set_mode("bogus")  # 未知模式忽略
    assert _prompt_text(tui) == "✨ > "


def test_plan_mode_syncs_input_prompt():
    tui = _tui()
    tui.set_plan_mode(True)
    assert tui._plan_mode is True
    assert _prompt_text(tui) == "📋 > "
    tui.set_plan_mode(False)
    assert tui._plan_mode is False
    assert _prompt_text(tui) == "✨ > "


def test_cycle_mode_rotates_agent_plan_shell():
    """Kimi Code 风格: Ctrl-X 循环切换 agent → plan → shell → agent。"""
    tui = _tui()
    assert tui._mode == "agent"
    tui.cycle_mode()
    assert tui._mode == "plan"
    assert tui._plan_mode is True
    assert _prompt_text(tui) == "📋 > "
    tui.cycle_mode()
    assert tui._mode == "shell"
    assert tui._plan_mode is False
    assert _prompt_text(tui) == "$ > "
    tui.cycle_mode()
    assert tui._mode == "agent"
    assert tui._plan_mode is False
    assert _prompt_text(tui) == "✨ > "
    assert any("[模式]" in e for e in tui._events)


def test_footer_renders_mode_badge():
    tui = _tui()
    tui.set_plan_mode(True)
    tui.add_tokens(1234)
    tui.set_context_pct(62.5)
    footer = "".join(text for _, text in tui._render_footer())
    assert "[plan]" in footer
    assert "[PLAN]" in footer
    assert "tok=1234" in footer
    assert "ctx=62%" in footer
