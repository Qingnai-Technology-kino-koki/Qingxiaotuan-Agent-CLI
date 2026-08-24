"""跨进程后台任务 manifest 的离线测试。"""

import json

from qingxiaotuan.core.background_store import BackgroundStore


def test_background_store_roundtrip_and_atomic_manifest(tmp_path):
    store = BackgroundStore(tmp_path)
    created = store.create("bg-test", "分析项目", str(tmp_path), "default")
    assert created["status"] == "queued"

    updated = store.update("bg-test", status="running", pid=1234, heartbeat=42.0)
    assert updated is not None
    assert updated["pid"] == 1234

    # 另一个 store 实例代表新的 CLI 进程，应能读取同一任务。
    other = BackgroundStore(tmp_path)
    loaded = other.get("bg-test")
    assert loaded is not None
    assert loaded["status"] == "running"
    assert loaded["pid"] == 1234
    assert not list((tmp_path / "background" / "jobs").glob("*.tmp"))


def test_background_store_persists_cancel_request(tmp_path):
    store = BackgroundStore(tmp_path)
    store.create("bg-cancel", "长任务", str(tmp_path))
    store.update("bg-cancel", status="cancel_requested")
    assert BackgroundStore(tmp_path).get("bg-cancel")["status"] == "cancel_requested"


def test_background_store_ignores_corrupt_manifest(tmp_path):
    store = BackgroundStore(tmp_path)
    corrupt = store.path("bg-corrupt")
    corrupt.write_text("{broken", encoding="utf-8")
    assert store.get("bg-corrupt") is None
    assert store.list() == []
