"""diff 引擎回归测试: 覆盖 merge3 双 offset 修复与 diff().changed 字段修复。"""
from qingxiaotuan.ext.diff_engine import DiffEngine


def test_diff_changed_false_when_identical():
    """内容相同: changed 必须为 False (旧实现恒为 True)。"""
    eng = DiffEngine()
    out = eng.diff({"old": "a\nb\nc\n", "new": "a\nb\nc\n"})
    assert out["added"] == 0
    assert out["removed"] == 0
    assert out["changed"] is False


def test_diff_changed_true_when_different():
    eng = DiffEngine()
    out = eng.diff({"old": "a\nb\n", "new": "a\nx\n"})
    assert out["added"] == 1
    assert out["removed"] == 1
    assert out["changed"] is True


def test_merge3_both_sides_non_conflicting():
    """ours 与 theirs 都有非冲突改动时, 两端都应并入 (修复前因 offset 重置而错位)。"""
    eng = DiffEngine()
    base = "a\nb\nc\nd\n"
    ours = "A\nb\nc\nd\n"        # 改第 0 行
    theirs = "a\nb\nc\nD\n"      # 改第 3 行
    out = eng.merge3({"base": base, "ours": ours, "theirs": theirs})
    assert out["conflicts"] == []
    assert out["result"] == "A\nb\nc\nD\n"


def test_merge3_interleaved_non_conflicting():
    """两侧改动在 base 坐标交错时仍应正确并入 (验证单遍按序合并)。"""
    eng = DiffEngine()
    base = "1\n2\n3\n4\n5\n"
    ours = "1\nX\n3\n4\n5\n"        # 改第 1 行
    theirs = "1\n2\n3\nY\n5\n"      # 改第 3 行
    out = eng.merge3({"base": base, "ours": ours, "theirs": theirs})
    assert out["conflicts"] == []
    assert out["result"] == "1\nX\n3\nY\n5\n"


def test_merge3_conflict_skips_overlapping():
    """同一区域两侧都改 → 标记为冲突且不并入任一改动 (保留 base 内容)。"""
    eng = DiffEngine()
    base = "a\nb\nc\n"
    ours = "a\nX\nc\n"          # 改第 1 行
    theirs = "a\nY\nc\n"        # 改第 1 行 (冲突)
    out = eng.merge3({"base": base, "ours": ours, "theirs": theirs})
    assert len(out["conflicts"]) == 1
    # 冲突区域不并入, 保留 base 行 "b"
    assert out["result"] == "a\nb\nc\n"


def test_merge3_theirs_only():
    eng = DiffEngine()
    base = "a\nb\nc\n"
    ours = "a\nb\nc\n"
    theirs = "a\nB\nc\n"
    out = eng.merge3({"base": base, "ours": ours, "theirs": theirs})
    assert out["result"] == "a\nB\nc\n"
    assert out["conflicts"] == []


def test_merge3_insert_insert_same_gap_conflict():
    """两侧在同一空隙的并发插入必须判冲突, 不能静默把两段都并入。

    回归背景: 旧实现用"行区间"判重叠, 插入 (i1==i2) 是空区间恒不冲突,
    结果对同位置双插入产生 A Y X B 这类错误并出, 而非报冲突。
    """
    eng = DiffEngine()
    base = "A\nB\n"
    out = eng.merge3({"base": base, "ours": "A\nX\nB\n", "theirs": "A\nY\nB\n"})
    assert len(out["conflicts"]) == 1
    # 冲突区域保留 base 内容, 不并入任何一侧
    assert out["result"] == "A\nB\n"


def test_merge3_insert_vs_replace_same_spot_conflict():
    """一侧插入命中另一侧替换的同一行 → 判冲突 (行-插入重叠)。"""
    eng = DiffEngine()
    base = "A\nB\nC\n"
    out = eng.merge3({"base": base, "ours": "A\nX\nB\nC\n", "theirs": "A\nY\nC\n"})
    # ours 在 gap1 插 X; theirs 把 base 第 1 行 B 替换成 Y → 触及同一位置冲突
    assert len(out["conflicts"]) >= 1
    assert "B" in out["result"]  # 冲突行不并入任一改动


def test_merge3_insert_gap_distinct_from_replace_line():
    """插入空隙与另一侧替换的不同行 → 非冲突, 双侧并入。"""
    eng = DiffEngine()
    base = "a\nb\nc\n"
    out = eng.merge3({"base": base, "ours": "a\nX\nb\nc\n", "theirs": "a\nb\nD\n"})
    assert out["conflicts"] == []
    assert out["result"] == "a\nX\nb\nD\n"


# ---------------------------------------------------------------- patch

# 将一个 a/b/c 文件第 2 行 b->X 的标准 unified diff
_VALID_PATCH = (
    "--- old\n"
    "+++ new\n"
    "@@ -1,3 +1,3 @@\n"
    " a\n"
    "-b\n"
    "+X\n"
    " c\n"
)


def test_patch_applies_matching_context():
    """上下文与源文件一致时应正确应用。"""
    eng = DiffEngine()
    out = eng.patch({"source": "a\nb\nc\n", "patch": _VALID_PATCH})
    assert out["applied"] is True
    assert out["result"] == "a\nX\nc\n"


def test_patch_rejects_context_mismatch():
    """源文件与 patch 上下文不符 (第 3 行被改成 Z) 必须 fail-closed。"""
    eng = DiffEngine()
    out = eng.patch({"source": "a\nb\nZ\n", "patch": _VALID_PATCH})
    assert out["applied"] is False
    assert "上下文" in out["error"]


def test_patch_rejects_short_source():
    """源文件比 hunk 期望的短 -> 上下文越界 -> fail-closed。"""
    eng = DiffEngine()
    out = eng.patch({"source": "a\nb\n", "patch": _VALID_PATCH})
    assert out["applied"] is False
    assert "越界" in out["error"]


def test_patch_roundtrip_via_diff():
    """回归: 引擎自身 diff 生成的 patch 应能应用回原内容。"""
    eng = DiffEngine()
    old = "alpha\nbeta\ngamma\ndelta\n"
    new = "alpha\nBETA\ngamma\ndelta\n"
    patch = eng.diff({"old": old, "new": new})["diff"]
    out = eng.patch({"source": old, "patch": patch})
    assert out["applied"] is True
    assert out["result"] == new

