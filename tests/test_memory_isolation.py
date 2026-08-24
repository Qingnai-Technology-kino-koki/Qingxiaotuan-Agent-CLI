"""记忆层写入异常隔离: 文件/FTS 失败不应向上抛出。"""
import sys
from pathlib import Path

import pytest

from qingxiaotuan.memory.store import MemoryStore


def test_append_memory_returns_line_even_if_fts_broken(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path, fts_enabled=True)
    # 模拟 FTS 写入抛异常: 直接让 _db 在 index 时不可用
    monkeypatch.setattr(store, "_db", None)  # 关闭 FTS, 模拟不可用
    line = store.append_memory("青小团用 opencode-zen 做弱模型")
    assert line.startswith("- [")
    assert "opencode-zen" in line
    # 文件仍写成功
    assert "opencode-zen" in (store.memory_file.read_text(encoding="utf-8"))


def test_update_user_swallows_index_error(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path, fts_enabled=True)
    # index 抛异常必须被吞掉
    def boom(*a, **k):
        raise RuntimeError("simulated fts failure")
    monkeypatch.setattr(store, "index", boom)
    out = store.update_user("preferred_model", "deepseek-chat")
    assert out == "preferred_model: deepseek-chat"
    assert "preferred_model" in store.read_user()


def test_search_returns_results(tmp_path):
    store = MemoryStore(tmp_path, fts_enabled=True)
    store.append_memory("用户喜欢用 Windows Terminal")
    hits = store.search("Windows Terminal")
    assert any("Windows Terminal" in h["content"] for h in hits)
