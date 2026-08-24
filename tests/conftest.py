import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.core.ipc_client import close_all_managers


@pytest.fixture(autouse=True)
def _cleanup_ipc_managers():
    """每个测试后关闭所有长驻外部引擎进程, 避免跨测试 IPC 句柄泄漏 (Windows 敏感)。"""
    yield
    try:
        close_all_managers()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def qxt_home(tmp_path, monkeypatch):
    """每个测试使用独立的 QXT_HOME, 不污染真实用户目录。"""
    home = tmp_path / ".qingxiaotuan"
    monkeypatch.setenv("QXT_HOME", str(home))
    yield home
