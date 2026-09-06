"""Splash → 主 TUI 切换 + TUI 上下文占用条回归测试。

锁定两个曾导致『只有吉祥物 + 100% 进度 + 无输入栏』的 bug:
  1. SplashTUI 在 Kernel 就绪 (或出错) 后必须退出 run(), 否则 fast_start 永远卡在
     启动屏, 主 TUI (含输入栏) 永远不会启动。
  2. QxtTUI 的上下文占用条在 budget 未知/为 0 时不得显示 300000%/100% 这种荒谬值,
     应回落到 0%; 真实估算/预算应算出正确百分比。
"""

import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fresh_qxt_home():
    os.environ["QXT_HOME"] = tempfile.mkdtemp()


def test_splash_exits_when_kernel_ready():
    """Kernel 就绪后 splash.run() 必须返回 (不再永久阻塞)。"""
    _fresh_qxt_home()
    from qingxiaotuan.ui.splash import SplashTUI

    splash = SplashTUI()
    splash.start()

    def _build():
        time.sleep(0.2)
        splash.set_kernel("FAKE_KERNEL")

    threading.Thread(target=_build, daemon=True).start()
    t0 = time.monotonic()
    splash.run()
    dt = time.monotonic() - t0
    assert splash.is_ready, "kernel 应已就绪"
    assert splash._closed, "splash 应在 kernel 就绪后关闭 (run 返回)"
    # 必须在合理时间内返回, 不能卡住
    assert dt < 5.0, f"splash.run() 耗时异常: {dt:.1f}s (疑似永久阻塞)"
    assert splash.kernel == "FAKE_KERNEL"


def test_splash_exits_on_error():
    """内核构建出错时 splash 也应退出, 让 fast_start 走错误分支返回。"""
    _fresh_qxt_home()
    from qingxiaotuan.ui.splash import SplashTUI

    splash = SplashTUI()
    splash.start()

    def _build():
        time.sleep(0.2)
        splash.set_error("boom")

    threading.Thread(target=_build, daemon=True).start()
    splash.run()
    assert splash.has_error, "应记录错误"
    assert splash._closed, "出错时 splash 也应关闭"


def test_tui_context_zero_when_budget_unknown():
    """budget=0 时上下文占用条必须显示 0%, 不得出现 300000%/100%。"""
    _fresh_qxt_home()
    from qingxiaotuan.tui.tui import QxtTUI

    tui = QxtTUI(on_submit=lambda t: "ok", title="青小团", workspace=os.getcwd())
    tui.set_context_info(3000, 0)
    assert tui._context_pct == 0.0, "budget=0 时占用应为 0%"
    assert tui._ctx_budget == 0, "budget=0 时内部预算应保持 0 (falsy)"
    s = tui._context_string()
    assert "300000" not in s, "不得出现 300000% 荒谬值"
    assert "100%" not in s, "budget 未知时不得显示 100%"
    # i18n: 输出可能是中文"上下文"或英文"context", 检查百分比值而非前缀
    assert "0%" in s or "0.0%" in s, f"footer 应含 0%, 实际: {s}"


def test_tui_context_real_percentage():
    """真实估算/预算应算出正确百分比 (夹在 0~100)。"""
    _fresh_qxt_home()
    from qingxiaotuan.tui.tui import QxtTUI

    tui = QxtTUI(on_submit=lambda t: "ok", title="青小团", workspace=os.getcwd())
    tui.set_context_info(3000, 60000)
    s = tui._context_string()
    assert "5%" in s or "5.0%" in s, f"应含 5%, 实际: {s}"
    assert "3.0k" in s, f"应含 3.0k, 实际: {s}"
    assert "60.0k" in s, f"应含 60.0k, 实际: {s}"

    tui.set_context_info(65000, 60000)
    s = tui._context_string()
    assert "100" in s, f"超预算应夹到 100%, 实际: {s}"
