"""自动检查点存储测试: 自动建点 / 持久化TTL / 三恢复模式 / 摘要 / 清理。"""

from __future__ import annotations

import os
import time

import pytest

from qingxiaotuan.core.checkpoint_store import CheckpointStore


class FakeLedger:
    def __init__(self):
        self._seq = 0

    def mark(self) -> int:
        self._seq += 1
        return self._seq


@pytest.fixture
def store(tmp_path):
    ledger = FakeLedger()
    s = CheckpointStore(str(tmp_path), ledger,
                        subdir=".qxt/checkpoints",
                        ttl=1000, max_checkpoints=3)
    return s


def test_auto_builds_checkpoints_and_tracks_files(store):
    store.auto_checkpoint(["a.py"], "edit a")
    store.auto_checkpoint(["b.py", "c.py"])
    cps = store.list()
    assert len(cps) == 2
    assert cps[0]["files"] == ["b.py", "c.py"]
    assert cps[0]["ledger_marker"] > 0
    assert (store.dir / f"cp_{cps[1]['id']}.md").exists()


def test_restore_code_mode_invokes_undo_cb(store):
    store.auto_checkpoint(["a.py"])
    last = store.list()[0]
    calls = []
    res = store.restore(last["id"], mode="code",
                        undo_cb=lambda m: calls.append(m) or [f"undo {m}"])
    assert calls == [last["ledger_marker"]]
    assert "文件回滚" in res


def test_restore_both_calls_undo_and_truncate(store, monkeypatch):
    store.auto_checkpoint(["a.py"])
    last = store.list()[0]
    calls = []
    res = store.restore(last["id"], mode="both",
                        undo_cb=lambda m: calls.append(("u", m)) or ["u"],
                        truncate_cb=lambda n: calls.append(("t", n)) or 5,
                        target_msg_len=5)
    assert ("u", last["ledger_marker"]) in calls
    assert ("t", 5) in calls


def test_restore_conversation_only_does_not_undo_code(store):
    store.auto_checkpoint(["a.py"])
    last = store.list()[0]
    calls = []
    res = store.restore(last["id"], mode="conversation",
                        undo_cb=lambda m: calls.append("u") or [],
                        truncate_cb=lambda n: 4,
                        target_msg_len=4)
    assert "u" not in calls
    assert "对话截回" in res


def test_restore_drops_checkpoints_after_target(store):
    store.auto_checkpoint(["a.py"])
    store.auto_checkpoint(["b.py"])
    store.auto_checkpoint(["c.py"])
    target = store.list()[2]  # 最早的 ckp1
    store.restore(target["id"],
                  undo_cb=lambda m: ["u"],
                  truncate_cb=lambda n: 3,
                  target_msg_len=3)
    assert store.list() == []
    assert store.find(target["id"]) is None


def test_ttl_prunes_expired(store):
    store.auto_checkpoint(["a.py"])
    # 手工把它的 created_at 改旧, 触发过期清理
    store._index[0]["created_at"] = time.time() - 5000
    store._flush()
    store._load()
    assert store.list() == []


def test_max_count_keeps_most_recent(store):
    for i in range(10):
        store.auto_checkpoint([f"{i}.py"])
    assert len(store.list()) == 3  # max_checkpoints=3


def test_summarize_reports_files(store):
    store.auto_checkpoint(["a.py", "b.py"], "feat")
    res = store.summarize_from(store.list()[0]["id"])
    assert "2 次文件变更" in res


def test_clear_removes_all(store):
    store.auto_checkpoint(["a.py"])
    store.auto_checkpoint(["b.py"])
    assert store.clear() == 2
    assert store.list() == []


def test_disabled_does_nothing(tmp_path):
    s = CheckpointStore(str(tmp_path), None, subdir=".qxt/checkpoints", ttl=100)
    s.auto=False
    assert s.auto_checkpoint(["a.py"]) is None
    assert s.list() == []


def test_index_persists_across_instances(tmp_path):
    s1 = CheckpointStore(str(tmp_path), FakeLedger(), subdir=".qxt/checkpoints", ttl=1000)
    s1.auto_checkpoint(["a.py"])
    s2 = CheckpointStore(str(tmp_path), FakeLedger(), subdir=".qxt/checkpoints", ttl=1000)
    assert len(s2.list()) == 1