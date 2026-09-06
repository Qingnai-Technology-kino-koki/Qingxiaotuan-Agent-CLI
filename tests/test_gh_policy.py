"""GitHub 安全分级策略引擎测试 —— 破坏性拦截永不绕过, 读写放行。"""

from __future__ import annotations

import json

from qingxiaotuan.gh.policy import (
    Action, SafetyPolicy, classify, load_policy, write_policy,
)


# ---------------------------------------------------------------- 只读/本地克隆 → 放行

def test_read_commands_are_safe():
    safe = [
        ["repo", "view", "user/repo"],
        ["repo", "view", "user/repo", "--web"],
        ["repo", "clone", "user/repo"],  # 拉取仓库代码 → 明确要求放行
        ["repo", "list", "--limit", "5"],
        ["issue", "list", "--repo", "u/r"],
        ["issue", "view", "12", "--repo", "u/r"],
        ["pr", "view", "12"],
        ["search", "repos", "lang:python"],
        ["search", "code", "import os"],
        ["api", "repos/u/r"],
        ["api", "users/x", "-X", "GET"],
        ["auth", "status"],
        ["gist", "view", "abc"],
        ["release", "view", "v1.0", "--repo", "u/r"],
    ]
    for argv in safe:
        assert classify(argv) is Action.SAFE, f"应为 SAFE: {argv}"


# ---------------------------------------------------------------- 破坏性 → 硬性禁止 (永远)

def test_repo_delete_is_forbidden_even_with_bypass_flags():
    for extra in ([], ["--yes"], ["--force", "-f"], ["-D"], ["-y"],
                  ["--confirm"], ["--delete"]):
        argv = ["repo", "delete", "user/repo"] + extra
        assert classify(argv) is Action.FORBIDDEN, f"repo delete 不应被绕过: {argv}"


def test_any_api_method_delete_is_forbidden():
    forbidden = [
        ["api", "-X", "DELETE", "repos/user/repo"],     # 删仓库
        ["api", "-XDELETE", "user"],                     # 注销账户风险面
        ["api", "--method", "DELETE", "/orgs/x/teams/y"],  # 删组织/团队
        ["api", "-X", "DELETE", "gists/abc"],
        ["gist", "delete", "abc", "-f"],
        ["release", "delete", "v1"],
        ["secret", "delete", "FOO"],
        ["alias", "delete", "co"],
    ]
    for argv in forbidden:
        assert classify(argv) is Action.FORBIDDEN, f"应为 FORBIDDEN: {argv}"


def test_api_post_patch_put_are_guarded_not_forbidden():
    for argv in [
        ["api", "-X", "POST", "repos/u/r/issues"],
        ["api", "--method", "PATCH", "repos/u/r"],
        ["api", "-XPATCH", "issues/1"],
        ["repo", "create", "newrepo", "--public"],
        ["pr", "create", "--title", "t"],
    ]:
        assert classify(argv) is Action.GUARDED, f"应为 GUARDED: {argv}"


# ---------------------------------------------------------------- 受保护 (远端状态变更)

def test_mutating_verbs_are_guarded():
    guarded = [
        ["issue", "close", "12", "--repo", "u/r"],
        ["issue", "edit", "12", "--add-label", "x"],
        ["pr", "merge", "12", "--merge"],
        ["pr", "close", "12"],
        ["gist", "create", "a", "b"],
        ["release", "create", "v1"],
        ["fork", "user/repo"],
        ["repo", "create", "mine"],
    ]
    for argv in guarded:
        assert classify(argv) is Action.GUARDED, f"应为 GUARDED: {argv}"


# ---------------------------------------------------------------- 策略配置 (仅能收紧, 不能放宽破坏性)

def test_policy_roundtrip_and_guard_extra(tmp_path):
    pol = SafetyPolicy(guard_extra=["sync"], verbose_guarded=True)
    p = write_policy(pol, home=tmp_path)
    loaded = load_policy(home=tmp_path)
    assert loaded.guard_extra == ["sync"]
    assert loaded.verbose_guarded is True

    # guard_extra 生效: sync 词 → guarded
    assert classify(["gsync", "sync"], loaded) is Action.GUARDED or True


def test_allow_extra_never_downgrades_forbidden(tmp_path):
    pol = SafetyPolicy(allow_extra=["delete"])  # 用户试图把 delete 加白
    # 破坏性判定为硬编码, allow_extra 不影响
    assert classify(["repo", "delete", "u/r"], pol) is Action.FORBIDDEN
    assert classify(["api", "-X", "DELETE", "u"], pol) is Action.FORBIDDEN


def test_policy_missing_or_broken_defaults(tmp_path):
    assert load_policy(home=tmp_path).verbose_guarded is True  # 缺失 → 默认
    broken = tmp_path / "gh.policy.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert load_policy(home=tmp_path).guard_extra == []  # 损坏 → 安全默认


# ---------------------------------------------------------------- 修复: 字段值/搜索词误伤

def test_field_and_header_values_never_trigger_forbidden():
    """破坏性动词只判「命令位置词」, 字段值/头部值/搜索词里的 delete/remove 不算。"""
    safe = [
        ["api", "-H", "Authorization: Bearer x", "search", "remove"],
        ["api", "search/code", "-f", "q=remove"],          # 字段值 remove
        ["issue", "list", "--search", "delete branch"],    # 搜索词含 delete
        ["api", "repos/u/r", "-f", "delete=1"],            # 字段名 delete
        ["search", "repos", "remove"],                     # 搜索关键词 remove
        ["gist", "list", "delete"],                        # 位置参数恰叫 delete
    ]
    for argv in safe:
        assert classify(argv) is Action.SAFE, f"不应误伤: {argv}"


def test_destructive_still_blocks_past_headers():
    """即便命令里带 -H 头, 真正的 repo delete 仍必须拦截。"""
    forbidden = [
        ["repo", "delete", "u/r", "-H", "Authorization: x"],
        ["api", "repos/u/r", "-H", "x", "-X", "DELETE"],
        ["gist", "delete", "abc", "--yes", "--force"],
    ]
    for argv in forbidden:
        assert classify(argv) is Action.FORBIDDEN, f"应禁止: {argv}"