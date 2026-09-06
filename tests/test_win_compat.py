# -*- coding: utf-8 -*-
"""win_compat 兼容层单测 —— 保证非 Windows / 非 TTY / 测试环境一律 no-op。"""
import sys

from qingxiaotuan.tui import win_compat as wc


def test_module_imports_and_constants():
    assert wc._ENABLE_VIRTUAL_TERMINAL_PROCESSING == 0x0004
    assert wc._CP_UTF8 == 65001


def test_is_windows_tty_false_in_test_env(monkeypatch):
    # 测试/CI 环境里无论平台都应判定为 False
    monkeypatch.setattr(wc, "_HAVE_CTYPES", True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    # 即便伪装成 Windows TTY, 测试环境仍因 PYTEST_CURRENT_TEST 被排除
    assert wc._is_windows_tty() is False


def test_enable_virtual_terminal_noop_outside_prod_tty():
    # 非生产 TTY 时, 多次调用都应返回 False 且不抛异常 (幂等)
    assert wc.enable_virtual_terminal() is False
    assert wc.enable_virtual_terminal() is False


def test_tui_imports_win_compat():
    # tui 包内应能安全导入 win_compat (跨平台)
    from qingxiaotuan.tui.tui import QxtTUI  # noqa: F401
    assert True


def test_fullscreen_imports_win_compat():
    # ui.fullscreen 引用 ..tui.win_compat, 需能成功导入 (路径正确性回归)
    import qingxiaotuan.ui.fullscreen  # noqa: F401
    assert True