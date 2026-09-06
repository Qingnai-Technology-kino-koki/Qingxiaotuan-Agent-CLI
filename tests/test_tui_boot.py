"""QxtTUI 启动引导 (loading) + 单 Application 切换的回归测试。

覆盖: 加载态渲染 / set_boot_progress / set_ready 切换 / boot_error 错误态。
这些逻辑与之前的『Splash→TUI 双全屏 App 切换闪退』直接相关, 必须稳定。
"""
import os
import sys

import pytest

from qingxiaotuan.tui.tui import QxtTUI


class _FakeCtx:
    confirm = None


class _FakeAgent:
    def __init__(self):
        self.ctx = _FakeCtx()
        self.plan_mode = False


class _FakeConfig:
    _data = {
        "model.provider": "opencode-zen",
        "model.model": "deepseek-v4-flash-free",
        "agent.effort": "high",
    }

    def get(self, key, default=None):
        return self._data.get(key, default)

    def api_key(self):
        return None


def _make_tui():
    return QxtTUI(
        lambda s: "ok",
        title="青小团",
        workspace=".",
        on_cancel=lambda: None,
        on_command=lambda c: None,
    )


def test_boot_starts_in_loading_state():
    tui = _make_tui()
    assert tui._booting is True
    assert tui._boot_error is None
    # 加载态下对话区应渲染加载屏 (含进度条/吉祥物), 而非欢迎横幅
    rendered = tui._render_conversation()
    flat = "".join(seg for _, seg in rendered)
    assert "█" in flat or "启动" in flat
    assert not any("Directory:" in e for e in tui._events)


def test_set_boot_progress_is_thread_safe_noop_when_not_running():
    tui = _make_tui()
    # 未运行事件循环时调用不应抛异常 (后台线程会调用)
    tui.set_boot_progress(42, "构建内核…")
    assert tui._boot_progress == 42
    assert tui._boot_status == "构建内核…"


def _event_text(e):
    """把事件归一化为纯文本: 兼容 str / (cls, text) 二元组 / 行分段列表 [(cls, text), …]。
    欢迎盒按行为列表事件追加(保证右边框对齐), 故需扁平化。"""
    if isinstance(e, str):
        return e
    if isinstance(e, list):
        return "".join(seg[1] for seg in e if isinstance(seg, (list, tuple)))
    return e[1] if e else ""


def test_set_ready_switches_to_chat_and_populates():
    tui = _make_tui()
    config = _FakeConfig()
    agent = _FakeAgent()
    tui.set_ready(config, agent, ".", tools=["read", "write", "shell"], no_key=True)

    assert tui._booting is False
    assert tui._boot_error is None
    joined = "\n".join(_event_text(e) for e in tui._events)
    # 欢迎横幅含工作区行 (ASCII 标签以规避 CJK 双宽导致的右边框错位)
    assert "Directory:" in joined
    assert "opencode-zen/deepseek-v4-flash-free" in joined
    # 工具列表已填充 (无侧栏, 存于 _tools)
    assert tui._tools == ["read", "write", "shell"]
    # 模型标签已设置
    assert tui._model_label == "opencode-zen/deepseek-v4-flash-free"
    # 确认回调已挂到 agent.ctx (方法每次访问都是新绑定对象, 故只校验可调用)
    assert agent.ctx.confirm is not None and callable(agent.ctx.confirm)


def test_boot_error_shows_error_state():
    tui = _make_tui()
    tui.boot_error("boom")
    assert tui._boot_error == "boom"
    assert tui._booting is False
    rendered = tui._render_conversation()
    flat = "".join(seg for _, seg in rendered)
    assert "boom" in flat
    # 退出提示随语言变化 (zh-CN: Ctrl-C 退出 / en: Ctrl-C exit), 统一断言 Ctrl 键提示存在
    assert "Ctrl" in flat


def test_run_submit_calls_on_submit_and_logs():
    calls = []
    tui = QxtTUI(
        lambda s: (calls.append(s) or f"echo:{s}"),
        title="青小团",
        workspace=".",
        on_cancel=lambda: None,
        on_command=lambda c: None,
    )
    tui.set_ready(_FakeConfig(), _FakeAgent(), ".")
    tui._busy = True
    tui._run_submit("你好")
    # on_submit 被调用, 返回值被记录到事件区, 且 _busy 复位
    assert calls == ["你好"]
    assert any("echo:你好" in e for e in tui._events)
    assert tui._busy is False


# ---- 上下文标注: 分母必须稳定 (此前的分母在「模型窗口/配置预算」间切换, 观感随机) ----

def test_context_uses_stable_model_window_denominator():
    tui = _make_tui()
    config = _FakeConfig()
    tui.set_ready(config, _FakeAgent(), ".")
    # set_ready 用模型上下文窗口作分母 (此处 provider=opencode-zen -> 默认 128k)
    budget0 = tui._ctx_budget
    assert budget0 > 0
    # 回合结束只刷新已用量, 分母不变
    tui.set_context_used(32_000)
    assert tui._ctx_tokens == 32_000
    assert tui._ctx_budget == budget0
    pct = 32000.0 / budget0 * 100
    assert abs(tui._context_pct - pct) < 0.01
    # 底层仍是确定性估算 (estimate 值即展示值, 无随机因素)
    tui.set_context_used(1_000)
    assert tui._ctx_tokens == 1_000  # 确定性地跟随传入值


# ---- 工具调用折叠: 多条 end_tool 折叠为一行 "see N", Ctrl+O 展开 ----


def _flat(tui):
    return "".join(seg for _, seg in tui._render_conversation())


def test_tool_calls_collapse_to_see_and_expand():
    tui = _make_tui()
    tui.set_ready(_FakeConfig(), _FakeAgent(), ".")
    for n in ("read_file", "list_dir", "edit_file"):
        tui.begin_tool(n)
        tui.end_tool(n, ok=True)
    # 折叠态: 三条工具记录不进 _events 逐条滚动, 只在对话区显示一行摘要
    assert len(tui._turn_tool_lines) == 3
    assert not any("read_file done" in str(e) for e in tui._events)
    assert "see 3 tool calls" in _flat(tui)
    # Ctrl+O 展开
    tui.toggle_audit()
    assert tui._tool_detail is True
    expanded = _flat(tui)
    assert "✓read_file done" in expanded and "✓edit_file done" in expanded
    # 审计面板同步记录了每一步
    texts = [t_ for _, t_ in tui._audit_log]
    assert any("read_file done" in t_ for t_ in texts)
    # 再折叠
    tui.toggle_audit()
    assert tui._tool_detail is False


def test_new_turn_resets_fold_and_refollows():
    tui = _make_tui()
    tui.set_ready(_FakeConfig(), _FakeAgent(), ".")
    tui.begin_tool("r")
    tui.end_tool("r", ok=True)
    tui._follow = False
    tui._pin = 2
    # 模拟新消息 + 空闲状态触发新一轮
    tui._busy = False
    tui._input.buffer.text = "hi"
    tui._handle_enter(type("_E", (), {
        "app": type("_A", (), {"invalidate": lambda self_: None})(),
        "current_buffer": tui._input.buffer,
    }))
    assert tui._turn_tool_lines == []
    assert tui._tool_detail is False
    assert tui._follow is True


def test_input_bar_uses_horizontal_scroll_not_wrap():
    """回归: 单行输入必须横向滚动 (wrap_lines=False)。

    若 wrap_lines=True 且 height=1, 中文/宽字符贴到右边界会触发裁剪式软换行,
    最后列宽核算错一位; 思考动画的高频全屏重绘把这些错位残影卷进输入栏,
    表现为 IME 打字/粘贴/英文输入全变乱码。
    """
    tui = _make_tui()
    assert tui._input.wrap_lines is False
    # 超长中文 + emoji + 英文混排在单行高度下也能安全写入/读取 (构造不抛异常)
    tui._input.text = "中文输入法测试🌑emoji混排哦哦哦哦哦哦哦哦哦哦哦哦哦哦哦 " * 20
    assert len(tui._input.text) > 200


# ---- 滚动: 内容超窗时, follow 钉底部, 上翻后 pin 生效, 到底恢复 follow ----

def test_scroll_pin_and_follow_to_bottom():
    tui = _make_tui()
    tui.set_ready(_FakeConfig(), _FakeAgent(), ".")
    for i in range(40):  # 造足够多的行
        tui.append_log(f"line {i}")
    n = len(tui._scrolled_lines(logic_only=True))
    assert n >= 40
    # 默认跟随底部
    assert tui._follow is True
    # 上翻一页 -> 离开跟随, pin 生效
    tui._scroll_log(-101)
    assert tui._follow is False
    assert 0 <= tui._pin < n
    # 大幅下滚 -> 到达底部 -> 恢复跟随
    tui._scroll_log(n)
    assert tui._follow is True


# ---- 输入框历史: 连续重复输入不入史, ↑/↓ 回看更干净 ----

def test_history_dedups_consecutive_duplicates():
    tui = _make_tui()
    h = tui._input.buffer.history
    tui._push_history("hi")
    tui._push_history("hi")
    tui._push_history("hello")
    tui._push_history("hi")   # 非连续同串仍保留 (中间隔了 hello)
    assert h.get_strings() == ["hi", "hello", "hi"]


def test_history_dedup_via_enter_same_text():
    tui = _make_tui()
    tui.set_ready(_FakeConfig(), _FakeAgent(), ".")
    fake_app = type("_A", (), {"invalidate": lambda self_: None})()
    ev = type("_E", (), {"app": fake_app, "current_buffer": tui._input.buffer})
    for _ in range(3):
        tui._input.buffer.text = "repeat me"
        tui._busy = False          # 每轮解锁, 让同串能再提交
        tui._handle_enter(ev)      # 真实回车路径: 入史 + 重置输入框
        assert tui._input.buffer.text == ""
    assert tui._input.buffer.history.get_strings() == ["repeat me"]
