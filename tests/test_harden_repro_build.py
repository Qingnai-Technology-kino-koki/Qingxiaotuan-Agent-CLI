"""可复现构建锁测试。"""
import os
from pathlib import Path

import pytest

from qingxiaotuan.harden import repro_build


def test_generate_and_verify_lock(tmp_path, monkeypatch):
    freeze = [
        "requests==2.31.0",
        "rich==13.7.0",
        "pyyaml==6.0.1",
    ]
    monkeypatch.setattr(repro_build, "_pip_freeze", lambda: list(freeze))

    lock = tmp_path / "requirements.lock"
    path = repro_build.generate_lock(str(lock))
    content = Path(path).read_text(encoding="utf-8")
    assert "# lock-hash:" in content
    for line in freeze:
        assert line in content

    ok, diff = repro_build.verify_lock(str(lock))
    assert ok is True and diff == []


def test_verify_detects_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(repro_build, "_pip_freeze", lambda: ["rich==13.7.0"])
    lock = tmp_path / "requirements.lock"
    repro_build.generate_lock(str(lock))

    # 模拟环境多出一个依赖 -> 漂移
    monkeypatch.setattr(repro_build, "_pip_freeze",
                        lambda: ["rich==13.7.0", "newpkg==1.0.0"])
    ok, diff = repro_build.verify_lock(str(lock))
    assert ok is False
    assert any("newpkg==1.0.0" in d for d in diff)


def test_verify_missing_lock(tmp_path):
    ok, diff = repro_build.verify_lock(str(tmp_path / "nope.lock"))
    assert ok is False
    assert diff
