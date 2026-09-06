"""qingxiaotuan.code_edit 模块测试。

覆盖：任务模板解析、五层安全闸门、版本锁死、权限 TTL/回收、验证闭环、规则外置、编排器。
"""

import time

from qingxiaotuan.code_edit import (
    Action, CodeEditAssistant, EditReport, PermissionGrant, RiskLevel, TaskSpec,
    UserPrefsMemory, VerifyLoop, check_version_lock, classify_risk,
    load_project_rules, parse_task_spec, run_tests,
)
from qingxiaotuan.code_edit.safety_gate import SafetyGate


# ---------------- task_spec ----------------
def test_parse_structured_markers():
    text = """
## Goal（目标）
修复登录失败

## Context（上下文）
涉及 `src/auth.py`，报错 ValueError

## Constraints（约束）
依赖限制: requests

## Done when（完成标准）
pytest 通过
"""
    spec = parse_task_spec(text)
    assert "修复登录失败" in spec.goal
    assert "src/auth.py" in spec.context_files
    assert "requests" in spec.constraint_deps
    assert "pytest 通过" in spec.done_when
    assert spec.is_complete()


def test_parse_unstructured_goes_to_goal_and_missing():
    spec = parse_task_spec("帮我把这个 bug 修一下")
    assert spec.goal == "帮我把这个 bug 修一下"
    assert not spec.is_complete()
    assert set(spec.missing()) >= {"Context（上下文）", "Constraints（约束）", "Done when（完成标准）"}


def test_partial_fills_missing():
    spec = parse_task_spec("目标: 加缓存",
                           {"context": "ctx", "constraints": "不改 API", "done_when": "测试通过"})
    assert spec.goal == "加缓存"
    assert spec.constraints == "不改 API"
    assert spec.context == "ctx"
    assert spec.is_complete()


def test_render_contains_sections():
    spec = parse_task_spec("## Goal\nX\n## Context\nY\n## Constraints\nZ\n## Done when\nW")
    out = spec.render()
    assert "Goal（目标）" in out and "Context（上下文）" in out
    assert "Constraints（约束）" in out and "Done when（完成标准）" in out


# ---------------- safety_gate ----------------
def test_classify_risk_levels():
    assert classify_risk(Action("edit_code", "改函数")).value >= RiskLevel.MEDIUM.value
    assert classify_risk(Action("delete_files", "删", ["a.py"])).value >= RiskLevel.HIGH.value
    assert classify_risk(Action("git_push_force", "强推")).value == RiskLevel.CRITICAL.value
    assert classify_risk(Action("run_command", "ls", command="rm -rf /")).value >= RiskLevel.HIGH.value


def test_gate_requires_confirm_high_risk():
    g = SafetyGate(confirm_fn=lambda p: True)
    assert g.requires_confirmation(Action("delete_files", "删", ["a"])) is True
    assert g.requires_confirmation(Action("edit_code", "改")) is False


def test_gate_confirm_denied_blocks():
    g = SafetyGate(confirm_fn=lambda p: False)  # fail-safe 默认拒绝
    assert g.confirm(Action("delete_files", "删", ["a"])) is False
    kinds = [e.layer for e in g.audit_trail]
    assert "confirm" in kinds


def test_gate_recheck_flags_uncommitted():
    g = SafetyGate()
    warns = g.recheck(Action("delete_files", "删", ["a"], uncommitted=True))
    assert any("未提交" in w for w in warns)


def test_permission_grant_ttl_and_revoke():
    g = SafetyGate()
    grant = g.grant("delete:tmp", ttl=0.05)
    assert g.is_valid(grant.token) is True
    time.sleep(0.08)
    assert g.is_valid(grant.token) is False  # 过期
    g2 = SafetyGate()
    grant2 = g2.grant("edit", ttl=300)
    assert g2.revoke(grant2.token) is True
    assert g2.is_valid(grant2.token) is False


def test_version_lock_semver():
    ok, reason = check_version_lock("1.2.3", "1.2.4")
    assert ok and "合规" in reason
    bad, r2 = check_version_lock("1.2.3", "1.2.2")
    assert not bad and "回退" in r2
    # 正式版回退到预发布
    bad2, r3 = check_version_lock("1.2.3", "1.2.3-rc1")
    assert not bad2
    # 主版本跃升允许但提示破坏性
    ok3, r4 = check_version_lock("1.2.3", "2.0.0")
    assert ok3 and "破坏性" in r4
    # 缺新值
    bad3, _ = check_version_lock("1.2.3", None)
    assert not bad3


# ---------------- verify_loop ----------------
def test_run_tests_passes():
    res = run_tests("python -c \"print('1 passed')\"")
    # 无 pytest 摘要，返回码 0 兜底为 passed=1
    assert res.ok


def test_run_tests_failure_parsed(tmp_path):
    script = tmp_path / "t.py"
    script.write_text("import sys; sys.exit(1)")
    res = run_tests(f"python {script}")
    assert not res.ok
    assert res.failed >= 1 or res.returncode != 0


def test_verify_loop_retries_on_failure():
    calls = {"n": 0}

    def fake_run(cmd, cwd):
        calls["n"] += 1
        from qingxiaotuan.code_edit import TestResult
        # 第一次失败，诊断后第二次成功
        if calls["n"] == 1:
            return TestResult(1, 0, 1, 0, "boom")
        return TestResult(0, 1, 0, 0, "ok")

    fixed = {"diagnose": 0}

    def diagnose(res):
        fixed["diagnose"] += 1

    loop = VerifyLoop(run_fn=fake_run, diagnose_fn=diagnose, max_retries=3)
    res = loop.run("pytest")
    assert res.ok
    assert calls["n"] == 2
    assert fixed["diagnose"] == 1


# ---------------- rules ----------------
def test_load_project_rules(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# 项目规则\n不许用 tab")
    text = load_project_rules(tmp_path)
    assert "不许用 tab" in text


def test_user_prefs_rejects_facts():
    mem = UserPrefsMemory()
    mem.set_pref("style", "简洁")
    assert mem.get_pref("style") == "简洁"
    try:
        mem.store("fact", "src/main.py", "改动")
        assert False, "应拒绝存项目事实"
    except ValueError:
        pass


# ---------------- assistant 编排 ----------------
def test_assistant_plan_completes_with_ask():
    def ask(missing):
        return {"context": "ctx", "constraints": "cons", "done_when": "done"}

    a = CodeEditAssistant(ask_fn=ask)
    spec = a.plan("目标: 改登录")
    assert spec.is_complete()
    assert spec.context == "ctx"


def test_assistant_denies_high_risk_without_confirm():
    a = CodeEditAssistant(confirm_fn=lambda p: False)  # fail-safe
    report = a.run(
        "## Goal\n删文件\n## Context\nx\n## Constraints\ny\n## Done when\nz",
        action=Action("delete_files", "删", ["a.py"], uncommitted=True),
    )
    assert report.denied
    assert "拦截" in report.deny_reason


def test_assistant_runs_verify_loop():
    calls = {"n": 0}

    def fake_run(cmd, cwd):
        calls["n"] += 1
        from qingxiaotuan.code_edit import TestResult
        return TestResult(0, 1, 0, 0, "ok")

    a = CodeEditAssistant(run_test_fn=fake_run, confirm_fn=lambda p: True)
    report = a.run(
        "## Goal\n改\n## Context\nx\n## Constraints\ny\n## Done when\nz",
        test_cmd="pytest",
        action=Action("edit_code", "改"),
    )
    assert report.verify is not None and report.verify.ok
    assert calls["n"] == 1


def test_assistant_version_lock_blocks():
    a = CodeEditAssistant(confirm_fn=lambda p: True)
    report = a.run(
        "## Goal\n发版\n## Context\nx\n## Constraints\ny\n## Done when\nz",
        action=Action("bump_version", "升版本", version_old="1.2.3", version_new="1.2.2"),
    )
    assert report.denied
    assert "版本锁死" in report.deny_reason
