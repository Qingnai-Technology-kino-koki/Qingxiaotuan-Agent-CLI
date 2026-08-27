"""UI 增强: 长输出折叠预览 + token/上下文状态栏回写。"""
from qingxiaotuan.ui.repl import UI
from qingxiaotuan.ui.mascot import IDLE


def _ui() -> UI:
    u = UI()
    u._last_collapsed = None  # 重置折叠缓冲
    return u


def test_collapse_long_multiline_tool_result():
    u = _ui()
    # 构造 30 行、每行 40 字符 -> 远超阈值, 应折叠
    big = "\n".join(f"line {i:02d} " + "x" * 36 for i in range(30))
    # tool_result 直接打印到 console, 这里只验证内部折叠逻辑分支不报错且缓冲被写入
    u.tool_result("cat", big)
    assert u._last_collapsed == big


def test_short_tool_result_not_collapsed():
    u = _ui()
    u.tool_result("echo", "hello world")
    assert u._last_collapsed is None


def test_failed_result_never_collapsed():
    u = _ui()
    big = "\n".join(f"err line {i} " + "y" * 36 for i in range(30))
    u.tool_result("run", big + "\nError: boom")
    # 失败结果走扁平分支, 不进折叠缓冲
    assert u._last_collapsed is None


def test_show_more_expands_and_clears():
    u = _ui()
    big = "\n".join(f"l{i} " + "z" * 36 for i in range(20))
    u._last_collapsed = big
    u.show_more()
    assert u._last_collapsed is None


def test_show_more_without_content():
    u = _ui()
    # 无内容时不应抛异常
    u.show_more()
    assert u._last_collapsed is None


def test_answer_md_collapses_very_long():
    u = _ui()
    huge = "# 大报告\n\n" + ("段落内容 " * 100 + "\n") * 40
    u.answer_md(huge)
    assert u._last_collapsed == huge


def test_add_tokens_and_context_pct():
    u = _ui()
    assert u._tokens == 0
    u.add_tokens(1234)
    assert u._tokens == 1234
    u.add_tokens(10)
    assert u._tokens == 1244
    # 上下文百分比应被钳制在 0~100
    u.set_context_pct(150.0)
    assert u._ctx_pct == 100.0
    u.set_context_pct(-5.0)
    assert u._ctx_pct == 0.0
    u.set_context_pct(42.5)
    assert u._ctx_pct == 42.5


def test_status_bar_reflects_tokens():
    u = _ui()
    u._mascot.set(IDLE)
    u.add_tokens(500)
    u.set_context_pct(33.0, used=33000, budget=100000)
    # status_bar 不应抛错, 且 context 占用与吉祥物图标出现在输出中
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        u.status_bar("standard", "high", "/workspace")
    rendered = buf.getvalue()
    assert "context: 33% (33k/100k)" in rendered
    assert "◦" in rendered  # 吉祥物状态图标保留在状态栏


# ----------------------------------------------------- 快捷键面板

def test_keymap_groups_have_slash_and_keys():
    u = _ui()
    all_keys = [k for _, items in u._key_groups() for k, _ in items]
    # 同时包含普通键位与斜杠命令
    assert "/help" in all_keys
    assert "Enter" in all_keys
    assert "Ctrl + G" in all_keys
    # 斜杠命令回填项存在
    slash = [k for k in all_keys if k.startswith("/")]
    assert len(slash) >= 5


def test_keymap_fallback_prints_without_tty():
    u = _ui()
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        u._print_keymap_fallback()
    out = buf.getvalue()
    assert "快捷键 / 命令面板" not in out  # fallback 不含标题(标题仅 GUI 面板用)
    assert "Enter" in out and "/help" in out


def test_keymap_panel_safe_in_headless():
    u = _ui()
    # 无 TTY 环境下 app.run() 会抛异常, 方法应降级到 fallback 并返回 None, 不崩溃
    res = u.keymap_panel()
    assert res is None


# ----------------------------------------------------- Kimi 风格 banner

def test_banner_renders_kimi_style_box():
    u = _ui()
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    cfg = {"session_id": None}
    with redirect_stdout(buf):
        u.banner(cfg, "C:/Users/28726", "deepseek-chat", "default",
                 mode="standard", effort="high")
    out = buf.getvalue()
    # 关键元素: 双线盒边框 + 欢迎语 + 字段行
    assert "Welcome to 青小团 CLI!" in out
    assert "Directory:" in out
    assert "Session:" in out
    assert "Model:" in out
    assert "Version:" in out
    assert "▐█▛█▛█▌" in out  # 品牌装饰块
    # 不再画 ASCII 团子吉祥物 (旧版用 Mascot.ascii 的 ( ◡ ) 等)
    assert "( ◡ )" not in out
    # 底部状态栏仍带吉祥物状态图标 (动态吉祥物保留在状态栏)
    assert "◦" in out or "●" in out or "◍" in out

