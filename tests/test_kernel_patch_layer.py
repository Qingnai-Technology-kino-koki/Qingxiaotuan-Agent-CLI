"""内核补丁层回归测试: 应用/回滚/缓存语义/事件/版本跳过。

设计目标: 补丁层必须 fail-open、可回滚、语义与未补丁时逐字节一致。
"""

from __future__ import annotations

import pytest

from qingxiaotuan.kernel_patch import install, ensure_builtin_loaded
from qingxiaotuan.kernel_patch import patch_base


@pytest.fixture(autouse=True)
def _clean_patches():
    """每个用例前回滚所有补丁, 保证隔离。"""
    mgr = patch_base.get_manager()
    mgr.revert_all()
    mgr._skipped.clear()
    yield
    mgr.revert_all()
    mgr._skipped.clear()


def test_builtin_patches_apply():
    mg = install()
    st = mg.status()
    assert st["applied"] == [
        "_i18n_lang_clear", "_i18n_t_memo", "_retry_classify_import_cache"]
    assert "_demo_always_bypassed" in st["skipped"], "版本过高的补丁应被跳过"


def test_i18n_t_memo_keeps_semantics():
    from qingxiaotuan import i18n as I
    install()
    I.set_language("zh-CN")
    v1 = I.t("banner.welcome")
    v2 = I.t("banner.welcome")          # 命中缓存
    assert v1 == v2
    assert "62" in I.t("fs.context_pct", pct=62)   # 插值仍正确


def test_lang_switch_clears_cache():
    from qingxiaotuan import i18n as I
    install()
    I.set_language("zh-CN")
    zh = I.t("banner.welcome")
    I.set_language("en")
    en = I.t("banner.welcome")
    assert zh != en, "语言切换后不应复用旧语种缓存"


def test_classify_error_equivalent():
    from qingxiaotuan.core.retry import classify_error
    install()
    assert classify_error(None) == ("other", None)


def test_revert_restores_and_emits():
    from qingxiaotuan.core.kernel import Kernel
    k = Kernel()
    mgr = patch_base.get_manager(k)
    ensure_builtin_loaded()
    a, _ = mgr.apply_all()
    assert a == 3
    assert sum(1 for e in k.events if e.type == "patch.applied") == 3
    n = mgr.revert_all()
    assert n == 3
    assert mgr.status()["patched_targets"] == []
    assert sum(1 for e in k.events if e.type == "patch.reverted") == 3


def test_fail_open_on_bad_target():
    """未知目标不应拖垮 apply_all, 只记录 skip。"""
    mgr = patch_base.KernelPatchManager()
    bad = patch_base.PatchSpec(
        name="bad_target", target="nonexistent.nope.fn",
        impl=lambda *a, **k: None, mode="wrap")
    mgr.register(bad)
    ok, reason = mgr.apply("bad_target")
    assert ok is False
    assert reason  # 有原因但不抛异常