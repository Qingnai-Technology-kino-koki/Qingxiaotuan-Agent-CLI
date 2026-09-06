"""上下文层测试: 分层上下文 + 事件溯源 + 分叉重放 + 技能蒸馏。"""
import pytest

from qingxiaotuan.arch.context import (
    ContextEventLog,
    SkillDistiller,
    TieredContext,
    fork_and_replay,
)


def test_tiered_context_cache_anchors_and_compaction():
    ctx = TieredContext(budget_tokens=5000, hot_capacity=4)
    ctx.anchor_system("SYSTEM PROMPT")  # 稳定前缀锚点
    for i in range(10):
        ctx.add("user", f"message number {i}")
    # 超出 hot 容量 -> 应发生压缩
    assert ctx.compactions >= 1
    rendered = ctx.render()
    # warm(含锚点) 在 hot 之前, 锚点带 cache_control
    assert rendered[0]["cache_control"] == {"type": "ephemeral"}
    assert ctx.stats()["cache_friendly"]


def test_event_log_and_fork_replay():
    log = ContextEventLog()
    for i in range(5):
        log.record("tool.executed", {"name": f"t{i}", "status": "ok"})
    assert log.count() == 5
    # 在 seq=3 处分叉并重放到子日志
    fork_id, child = fork_and_replay(log, at_seq=3, reason="try-b")
    # 子日志应含 seq>3 的事件 (t3, t4)
    replayed = child.query()
    assert len(replayed) == 2
    assert {e.payload.get("name") for e in replayed} == {"t4", "t3"}


def test_skill_distiller_finds_frequency_pattern():
    log = ContextEventLog()
    # 高频 pattern: search -> read, 出现 3 次
    seq = [("search", "read"), ("search", "read"), ("search", "read"), ("write", "delete")]
    for a, b in seq:
        log.record("tool.executed", {"name": a, "status": "ok"})
        log.record("tool.executed", {"name": b, "status": "ok"})
    distiller = SkillDistiller(enable_native=False)
    cands = distiller.distill(log, min_frequency=2)
    names = {c.name for c in cands}
    assert any("search-then-read" in n for n in names)
    top = cands[0]
    assert top.frequency >= 2


def test_skill_distiller_ngram_patterns():
    """SkillDistiller 应能发现 3-gram 工作流模式。"""
    log = ContextEventLog()
    # 高频 3-gram: search -> read -> edit, 出现 3 次
    for _ in range(3):
        log.record("tool.executed", {"name": "search", "status": "ok"})
        log.record("tool.executed", {"name": "read", "status": "ok"})
        log.record("tool.executed", {"name": "edit", "status": "ok"})
    distiller = SkillDistiller(enable_native=False)
    cands = distiller.distill(log, min_frequency=2, max_ngram=3)
    # 应发现 3-gram 模式
    three_gram = [c for c in cands if len(c.source_tools) == 3]
    assert len(three_gram) >= 1
    assert three_gram[0].source_tools == ["search", "read", "edit"]
    assert three_gram[0].frequency == 3
    # 3-gram 置信度应高于 2-gram
    assert three_gram[0].confidence >= 0.65


def test_replay_into_existing_child():
    parent = ContextEventLog()
    for i in range(4):
        parent.record("tool.executed", {"name": f"t{i}", "status": "ok"})
    child = ContextEventLog(session_id="child")
    child.record("tool.executed", {"name": "seed", "status": "ok"})
    fork_id, child2 = fork_and_replay(parent, at_seq=2, child=child)
    # child 已有 seed, 再重放 t2/t3 (seq>2) -> 重放事件应存在
    names = {e.payload.get("name") for e in child2.query()}
    assert {"t2", "t3"}.issubset(names)
    assert "seed" in names
