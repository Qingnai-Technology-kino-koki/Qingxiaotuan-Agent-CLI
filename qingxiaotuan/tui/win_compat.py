# -*- coding: utf-8 -*-
"""Windows 原生终端兼容层 (评审 Major #6)。

prompt_toolkit 在 Windows 上依赖 VT (虚拟终端/ANSI) 转义序列渲染颜色与刷新。若控制台
句柄未开启 ``ENABLE_VIRTUAL_TERMINAL_PROCESSING``, 或输出代码页不是 UTF-8, 中文/
emoji/框线在多字节宽度下会被 ConHost 渲染成乱码或挤位。本模块在 Windows + TTY 下:

- 用 ctypes 对 stdout/stderr 控制台句柄打开 VT 处理旗标;
- 把活动输出代码页切到 UTF-8 (65001), 让 CJK 正确落位。

非 Windows / 非 TTY / 测试环境一律 no-op, 保证可无副作用 import (提示: 改变代码页
只在真正进入全屏 TUI 前调用, 不影响普通 CLI 输出)。启用失败的句柄/代码页会记
warning 日志, 便于排查 ConHost 乱码。
"""
import logging
import os
import sys
import threading

try:
    import ctypes
    import ctypes.wintypes
    _HAVE_CTYPES = True
except Exception:  # pragma: no cover - 非 Windows / ctypes 不可用
    _HAVE_CTYPES = False

log = logging.getLogger(__name__)

_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_STDOUT_HANDLE = -11
_STDERR_HANDLE = -12
_CP_UTF8 = 65001

_initialized = False
_lock = threading.Lock()


def _is_windows_tty() -> bool:
    return (
        sys.platform == "win32"
        and _HAVE_CTYPES
        and getattr(sys.stdout, "isatty", lambda: False)()
        and not os.environ.get("PYTEST_CURRENT_TEST")
        and not os.environ.get("CI")
    )


def _enable_vt_on(handle: int) -> bool:
    """为指定标准句柄打开 VT 处理旗标; 失败记 warning 并返回 False。"""
    try:
        kernel32 = ctypes.windll.kernel32
        h = kernel32.GetStdHandle(handle)
        if h in (None, -1, 0):
            log.warning("VT enable: 无效标准句柄 (%s -> %r)", handle, h)
            return False
        mode = ctypes.wintypes.DWORD()
        if not kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            log.warning("VT enable: GetConsoleMode 失败 (errno=%s)", ctypes.get_last_error())
            return False
        kernel32.SetConsoleMode(h, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        return True
    except Exception as exc:  # pragma: no cover - 句柄不可用的极边缘环境
        log.warning("VT enable: 异常 %s", exc)
        return False


def _set_utf8_codepage() -> None:
    """把控制台输出代码页切到 UTF-8 (Windows Terminal 已是 UTF-8, 幂等; 失败记日志)。"""
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(_CP_UTF8)
        ctypes.windll.kernel32.SetConsoleCP(_CP_UTF8)
    except Exception as exc:  # pragma: no cover
        log.warning("code page 切换为 UTF-8(65001) 失败: %s", exc)


def _reconfigure_stdio_utf8() -> None:
    """把 Python 已打开的 stdin/stdout/stderr 重配为 UTF-8 (best-effort)。

    SetConsoleCP/SetConsoleOutputCP 只改控制台代码页, 不会更新解释器在启动时
    就开好的 StreamWrapper 的编码; 用 reconfigure 兜底, 否则经原生 stdio 打印/
    读入的中文在 GBK(cp936) 环境下仍会乱码。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if stream is not None and getattr(stream, "isatty", lambda: False)():
                stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 - 非 TTY / 不支持 reconfigure 时忽略
            pass


def enable_virtual_terminal() -> bool:
    """在 Windows + TTY 下启用 VT 处理与 UTF-8 代码页; 返回本次是否真执行。

    幂等 (线程安全): 只首次真正执行。非 Windows / 非 TTY / 测试环境返回 False。
    """
    global _initialized
    if _initialized:
        return False
    if not _is_windows_tty():
        return False
    with _lock:
        if _initialized:
            return False
        ok_stdout = _enable_vt_on(_STDOUT_HANDLE)
        _enable_vt_on(_STDERR_HANDLE)
        _set_utf8_codepage()
        _reconfigure_stdio_utf8()
        _initialized = True
        log.debug("Windows VT enabled: stdout=%s", ok_stdout)
        return True