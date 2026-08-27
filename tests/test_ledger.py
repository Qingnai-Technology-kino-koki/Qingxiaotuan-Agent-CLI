"""事务化操作账本 (MutationLedger) 与影响半径预览测试。

覆盖:
- 文件 写/改/删/移 的精确快照与回滚
- 撤销指定文件 / 撤销全部
- 工具异常时自动回滚 (事务保证)
- 事前影响半径预览生成
- ToolRegistry.dispatch_result 集成 (自动快照 + 记录 + 回滚)
- 账本跨进程持久化 (journal + 快照落盘, load_journal 后仍能恢复)
"""

import json
from pathlib import Path

from qingxiaotuan.core.ledger import MutationLedger
from qingxiaotuan.tools.base import (
    ToolContext, ToolRegistry, Tool, _build_impact_preview, _MUTATION_EXTRACTORS,
)


# ------------------------------------------------------------------ 桩件

class FakeKernel:
    """最小内核桩: 仅提供 config 与 no-op emit。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, service, default=None):
        return self._cfg if service == "config" else default

    def emit(self, *a, **k):
        pass


def _make_ctx(workspace: Path, ledger: MutationLedger):
    kernel = FakeKernel({"ledger": {"enabled": True, "impact_preview": True,
                                    "auto_rollback_on_error": True, "keep_snapshots": True}})
    return ToolContext(kernel=kernel, workspace=str(workspace), confirm=lambda p: True, ledger=ledger)


def _write(ctx, path, content):
    p = Path(ctx.workspace) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {path}"


def _edit(ctx, path, old_string, new_string):
    p = Path(ctx.workspace) / path
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    return f"edited {path}"


def _delete(ctx, path):
    p = Path(ctx.workspace) / path
    p.unlink()
    return f"deleted {path}"


def _move(ctx, src, dst):
    s = Path(ctx.workspace) / src
    d = Path(ctx.workspace) / dst
    d.parent.mkdir(parents=True, exist_ok=True)
    s.rename(d)
    return f"moved {src} -> {dst}"


# ------------------------------------------------------------------ 单元测试

def test_write_then_undo_removes_file(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("v1", encoding="utf-8")
    snaps = ledger.snapshot(["a.txt"])
    assert snaps[0]["existed"] is True
    ledger.record("write_file", ["a.txt"], snaps, summary="x")
    f.write_text("v2", encoding="utf-8")
    assert f.read_text() == "v2"
    ledger.undo_last(1)
    assert f.read_text() == "v1"


def test_created_file_undo_deletes_it(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    f = tmp_path / "new.txt"
    snaps = ledger.snapshot(["new.txt"])  # 文件不存在
    assert snaps[0]["existed"] is False
    ledger.record("write_file", ["new.txt"], snaps)
    f.write_text("hello", encoding="utf-8")
    assert f.exists()
    ledger.undo_last(1)
    assert not f.exists()


def test_delete_then_undo_restores(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("keep", encoding="utf-8")
    snaps = ledger.snapshot(["a.txt"])
    ledger.record("delete_file", ["a.txt"], snaps)
    f.unlink()
    assert not f.exists()
    ledger.undo_last(1)
    assert f.exists() and f.read_text() == "keep"


def test_move_then_undo_reverts(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    src = tmp_path / "s.txt"
    dst = tmp_path / "d.txt"
    src.write_text("data", encoding="utf-8")
    snaps = ledger.snapshot(["s.txt", "d.txt"])
    ledger.record("move_file", ["s.txt", "d.txt"], snaps)
    src.rename(dst)
    assert not src.exists() and dst.exists()
    ledger.undo_last(1)
    assert src.exists() and not dst.exists() and src.read_text() == "data"


def test_undo_file_targeted(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    fa, fb = tmp_path / "a.txt", tmp_path / "b.txt"
    fa.write_text("A", encoding="utf-8")
    fb.write_text("B", encoding="utf-8")
    sa = ledger.snapshot(["a.txt"]); ledger.record("edit_file", ["a.txt"], sa)
    sb = ledger.snapshot(["b.txt"]); ledger.record("edit_file", ["b.txt"], sb)
    fa.write_text("A2", encoding="utf-8")
    fb.write_text("B2", encoding="utf-8")
    res = ledger.undo_file("a.txt")
    assert res is not None
    assert fa.read_text() == "A"
    assert fb.read_text() == "B2"  # 仅 a 被撤销


def test_undo_all(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    fa, fb = tmp_path / "a.txt", tmp_path / "b.txt"
    fa.write_text("A", encoding="utf-8"); fb.write_text("B", encoding="utf-8")
    sa = ledger.snapshot(["a.txt"]); ledger.record("edit_file", ["a.txt"], sa)
    sb = ledger.snapshot(["b.txt"]); ledger.record("edit_file", ["b.txt"], sb)
    fa.write_text("A2", encoding="utf-8"); fb.write_text("B2", encoding="utf-8")
    done = ledger.undo_all()
    assert len(done) == 2
    assert fa.read_text() == "A" and fb.read_text() == "B"


def test_stats_and_history(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    fa = tmp_path / "a.txt"
    fa.write_text("A", encoding="utf-8")
    sa = ledger.snapshot(["a.txt"]); ledger.record("edit_file", ["a.txt"], sa, summary="edit")
    st = ledger.stats()
    assert st["records"] == 1 and st["files_touched"] == 1
    hist = ledger.history()
    assert hist[0]["tool"] == "edit_file" and hist[0]["summary"] == "edit"


# ------------------------------------------------------------------ 异常自动回滚 (事务保证)

def test_auto_rollback_on_handler_error(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("orig", encoding="utf-8")
    snaps = ledger.snapshot(["a.txt"])
    ledger.record("write_file", ["a.txt"], snaps)
    # 模拟 handler: 先改坏, 再抛异常
    f.write_text("corrupted", encoding="utf-8")
    ledger.restore(snaps)  # dispatch_result 在 except 分支会调用此
    assert f.read_text() == "orig"


# ------------------------------------------------------------------ 影响半径预览

def test_impact_preview_edit(tmp_path):
    ctx = _make_ctx(tmp_path, MutationLedger(str(tmp_path)))
    f = tmp_path / "a.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")
    preview = _build_impact_preview("edit_file",
                                    {"path": "a.txt", "old_string": "line1", "new_string": "LINE1"},
                                    ctx, ["a.txt"])
    assert "a.txt" in preview
    assert "[+]" in preview and "[-]" in preview


def test_impact_preview_write_new(tmp_path):
    ctx = _make_ctx(tmp_path, MutationLedger(str(tmp_path)))
    preview = _build_impact_preview("write_file",
                                    {"path": "new.py", "content": "x=1\n"}, ctx, ["new.py"])
    assert "新建" in preview and "new.py" in preview


def test_impact_preview_delete(tmp_path):
    ctx = _make_ctx(tmp_path, MutationLedger(str(tmp_path)))
    preview = _build_impact_preview("delete_file", {"path": "a.txt"}, ctx, ["a.txt"])
    assert "删除" in preview


def test_extractors_registered():
    for name in ("write_file", "edit_file", "delete_file", "delete_dir", "move_file"):
        assert name in _MUTATION_EXTRACTORS


# ------------------------------------------------------------------ dispatch 集成

def test_dispatch_auto_snapshot_and_undo(tmp_path):
    ledger = MutationLedger(str(tmp_path))
    reg = ToolRegistry()
    reg.register(Tool(name="write_file", handler=_write, dangerous=True, parameters={}, description="w"))
    ctx = _make_ctx(tmp_path, ledger)
    f = tmp_path / "a.txt"
    f.write_text("seed", encoding="utf-8")
    # 第一次写入 (覆盖)
    res1 = reg.dispatch_result("write_file", json.dumps({"path": "a.txt", "content": "v1"}), ctx)
    assert res1.status == "ok"
    assert f.read_text() == "v1"
    # 第二次写入
    reg.dispatch_result("write_file", json.dumps({"path": "a.txt", "content": "v2"}), ctx)
    assert f.read_text() == "v2"
    # 账本记录了 2 步, 撤销全部回到 seed
    assert ledger.stats()["records"] == 2
    ledger.undo_all()
    assert f.read_text() == "seed"


def test_dispatch_snapshot_then_rollback_on_error(tmp_path):
    """handler 抛异常时, 文件系统应回到执行前状态 (事务保证)。"""
    ledger = MutationLedger(str(tmp_path))
    reg = ToolRegistry()

    def bad_write(ctx, path, content):
        p = Path(ctx.workspace) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        raise RuntimeError("boom mid-write")

    reg.register(Tool(name="write_file", handler=bad_write, dangerous=True, parameters={}, description="w"))
    ctx = _make_ctx(tmp_path, ledger)
    f = tmp_path / "a.txt"
    f.write_text("before", encoding="utf-8")
    res = reg.dispatch_result("write_file", json.dumps({"path": "a.txt", "content": "after"}), ctx)
    assert res.status == "error"
    # 文件系统必须回到 before (异常自动回滚)
    assert f.read_text() == "before"


# ------------------------------------------------------------------ 跨进程持久化

def test_journal_durable_roundtrip(tmp_path):
    """账本落盘后, 新进程 load_journal 仍能精确恢复文件。"""
    ws = str(tmp_path)
    cfg = {"ledger": {"enabled": True, "keep_snapshots": True}}
    ledger1 = MutationLedger(ws, cfg)
    f = tmp_path / "a.txt"
    f.write_text("original", encoding="utf-8")
    snaps = ledger1.snapshot(["a.txt"])
    ledger1.record("edit_file", ["a.txt"], snaps, summary="edit")
    ledger1.persist()

    # 模拟另一个进程: 文件已被改, 用持久化账本恢复
    f.write_text("mutated", encoding="utf-8")
    ledger2 = MutationLedger(ws, cfg)
    n = ledger2.load_journal()
    assert n == 1
    ledger2.undo_last(1)
    assert f.read_text() == "original"


def test_snapshot_tags_unique_across_ledger_instances(tmp_path):
    """回归: 快照标签曾用纯实例内计数, 两个会话各自从同一序号起算,
    后写会话会用同名快照覆写先前会话的 bin, undo 恢复出错误版本。
    现标签带 time_ns, 跨实例必然不同名。"""
    ws = str(tmp_path)
    cfg = {"ledger": {"enabled": True, "keep_snapshots": True}}
    f = tmp_path / "a.txt"
    f.write_text("v1", encoding="utf-8")

    ledger1 = MutationLedger(ws, cfg)
    snaps1 = ledger1.snapshot(["a.txt"])
    ledger1.record("write_file", ["a.txt"], snaps1)

    # 模拟另一进程: 文件已被改成 v2, 新账本对同一文件再快照
    f.write_text("v2", encoding="utf-8")
    ledger2 = MutationLedger(ws, cfg)
    snaps2 = ledger2.snapshot(["a.txt"])
    ledger2.record("edit_file", ["a.txt"], snaps2)

    assert snaps1[0]["snap"] and snaps2[0]["snap"]
    names1 = {Path(s["snap"]).name for s in snaps1}
    names2 = {Path(s["snap"]).name for s in snaps2}
    assert names1.isdisjoint(names2)  # 旧实现此处失败: 同名快照被覆写

    # record 时已逐条追加 journal (勿调 persist, 其整文件重写会丢历史);
    # 第三个账本加载后应能完整撤销两步, 最终恢复出 v1
    ledger3 = MutationLedger(ws, cfg)
    assert ledger3.load_journal() == 2
    assert len(ledger3.undo_all()) == 2
    assert f.read_text(encoding="utf-8") == "v1"
