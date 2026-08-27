"""记忆/会话持久层回归: recall_context 去重、原子写、list_sessions 竞态兜底。"""
from __future__ import annotations

from pathlib import Path

import pytest

from qingxiaotuan.memory.sessions import SessionStore
from qingxiaotuan.memory.store import MemoryStore, _atomic_write_text


def test_recall_context_dedupes_fts_and_recent(tmp_path: Path) -> None:
    """同一记忆同时命中全文检索与近 24h 时, 只应出现一次 (合并去重)。"""
    store = MemoryStore(tmp_path, fts_enabled=True)
    store.append_memory("青小团的量子猫观测实验记录")
    out = store.recall_context("量子猫观测实验记录", limit=5)
    assert out, "recall_context 应召回至少一条"
    # 旧实现裸内容与带前缀条目比较, 去重永不生效, 同一条会出现 [记忆]+[近期] 两行
    assert out.count("量子猫观测实验记录") == 1


def test_recall_context_empty_when_no_match(tmp_path: Path) -> None:
    """无任何命中时返回空串, 不抛异常。"""
    store = MemoryStore(tmp_path, fts_enabled=True)
    assert store.recall_context("完全不存在的关键词组合xyz") == ""


def test_atomic_write_normal_roundtrip(tmp_path: Path) -> None:
    """_atomic_write_text 正常路径: 内容完整落盘, 无临时文件残留。"""
    target = tmp_path / "MEMORY.md"
    text = "- 第一条\n- 第二条\n"
    _atomic_write_text(target, text)
    assert target.read_text(encoding="utf-8") == text
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_replace_keeps_original_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """替换失败 (磁盘满等) 时原记忆文件必须完好无损, 且不留临时文件。"""
    store = MemoryStore(tmp_path, fts_enabled=False)
    store.append_memory("原有记忆不可丢失")
    original = store.read_memory()

    import qingxiaotuan.memory.store as mstore

    def boom(src: object, dst: object) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(mstore.os, "replace", boom)
    # 异常隔离: append_memory 吞掉 OSError, 绝不向上抛
    store.append_memory("崩溃前最后一条")
    assert store.read_memory() == original
    assert list(store.dir.glob("*.tmp")) == []


def test_list_sessions_tolerates_vanished_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """遍历间隙被清理的会话文件应跳过, 而不是让整个列表崩掉。"""
    s = SessionStore(tmp_path)
    alive = s.dir / "a.jsonl"
    alive.write_text("{}\n", encoding="utf-8")
    vanished = s.dir / "b.jsonl"
    vanished.write_text("{}\n", encoding="utf-8")

    real_stat = Path.stat

    def flaky_stat(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == "b.jsonl":
            raise FileNotFoundError(f"vanished: {self.name}")
        return real_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", flaky_stat)
    names = [p.name for p in s.list_sessions()]
    assert "a.jsonl" in names
    assert "b.jsonl" not in names


def test_build_task_context_recalls_recent_memory(tmp_path: Path) -> None:
    """build_task_context 应走 recall_context: 无关键词命中也召回近 24h 记忆。"""
    from qingxiaotuan.core.prompts import build_task_context

    store = MemoryStore(tmp_path, fts_enabled=True)
    store.append_memory("青小团的部署脚本藏在 scripts/deploy.ps1")
    out = build_task_context("今天天气怎么样", memory_store=store)
    assert "deploy.ps1" in out, "近期记忆应经 recall_context 注入任务上下文"


def test_memory_store_cross_thread_writes_indexed(tmp_path: Path) -> None:
    """跨线程写入不应静默丢失索引或丢行 (check_same_thread=False + 锁串行化)。"""
    import threading

    store = MemoryStore(tmp_path, fts_enabled=True)
    errors: list[str] = []

    def worker(i: int) -> None:
        try:
            store.append_memory(f"线程记忆条目编号{i}", section="并发")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    stats = store.get_stats()
    assert stats["fts_entries"] == 6, "跨线程写入必须全部进 FTS 索引"
    assert stats["tag_entries"] == 6
    text = store.read_memory()
    for i in range(6):
        assert f"线程记忆条目编号{i}" in text, "并发追加不允许丢行"
