"""记忆层增长保护 + 配置清理的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from qingxiaotuan.memory.store import MemoryStore


@pytest.fixture
def mem_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _write_lines(path: Path, n: int) -> None:
    lines = [f"- [{i}] fact number {i}" for i in range(n)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_append_memory_rotation(mem_home: Path) -> None:
    """append_memory 超过上限时裁剪最旧条目, 不丢新写入。"""
    store = MemoryStore(mem_home, fts_enabled=False)
    # 预填到接近上限
    _write_lines(store.memory_file, MemoryStore.MAX_MEMORY_LINES - 5)
    # 再追加 20 条, 应触发裁剪
    for i in range(20):
        store.append_memory(f"new fact {i}", section="测试")
    text = store.read_memory()
    lines = [l for l in text.splitlines() if l.startswith("- ")]
    # 上限 + 裁剪提示行, 不应无限膨胀
    assert len(lines) <= MemoryStore.MAX_MEMORY_LINES + 1
    # 最新一条一定在
    assert "new fact 19" in text
    # 最旧的已滚出
    assert "fact number 0" not in text


def test_update_user_rotation(mem_home: Path) -> None:
    """update_user 新增键超过上限时裁剪最旧字段。"""
    store = MemoryStore(mem_home, fts_enabled=False)
    _write_lines(store.user_file, MemoryStore.MAX_USER_LINES - 3)
    for i in range(10):
        store.update_user(f"field_{i}", f"value {i}")
    text = store.read_user()
    lines = [l for l in text.splitlines() if l.startswith("- ")]
    assert len(lines) <= MemoryStore.MAX_USER_LINES + 1
    assert "field_9" in text


def test_append_memory_isolation_on_error(mem_home: Path) -> None:
    """即使索引层抛异常, 文件写入仍成功 (异常隔离)。"""
    store = MemoryStore(mem_home, fts_enabled=False)
    # 用一个会触发 OSError 的路径不存在场景: 直接强行把文件设为只读目录
    bad = mem_home / "nope" / "MEMORY.md"
    # 模拟 write 失败: 替换路径指向一个文件但父目录不可写
    store.memory_file = bad
    # 不应抛异常, 返回原样 line
    line = store.append_memory("should not crash")
    assert line.startswith("- [")


def test_no_rotation_when_under_limit(mem_home: Path) -> None:
    """未超过上限时不裁剪, 顺序保持。"""
    store = MemoryStore(mem_home, fts_enabled=False)
    for i in range(5):
        store.append_memory(f"keep {i}")
    text = store.read_memory()
    assert "keep 0" in text and "keep 4" in text
    assert "[系统] 已自动裁剪" not in text
