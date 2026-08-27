"""自主反思循环 (Reflector) 单元测试。"""

from unittest.mock import MagicMock, patch

from qingxiaotuan.core.reflector import (
    ReflectDecision,
    Reflector,
    VerificationResult,
    Verifier,
)


# ------------------------------------------------------------------ Verifier: 工具检测

def test_detect_available_python(tmp_path):
    """存在 pyproject.toml + tests 目录时应检测到 pytest。"""
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    v = Verifier(str(tmp_path))
    available = v.detect_available()
    assert "pytest" in available
    assert "ruff" in available


def test_detect_available_mypy_flake8(tmp_path):
    """pyproject.toml 中出现 mypy/flake8 关键字应被检测。"""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\n[tool.ruff]\nflake8\n", encoding="utf-8")
    v = Verifier(str(tmp_path))
    available = v.detect_available()
    assert "mypy" in available
    assert "flake8" in available


def test_detect_available_node_go_rust(tmp_path):
    """package.json / go.mod / Cargo.toml 应触发对应验证工具。"""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".eslintrc.json").write_text("{}", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module x", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text("[package]", encoding="utf-8")
    v = Verifier(str(tmp_path))
    available = v.detect_available()
    assert "npm_test" in available
    assert "eslint" in available
    assert "go_test" in available
    assert "cargo_test" in available


def test_detect_available_empty_workspace(tmp_path):
    """空工作区不应检测到任何验证工具。"""
    v = Verifier(str(tmp_path))
    assert v.detect_available() == []


# ------------------------------------------------------------------ Verifier: 运行验证

def test_run_verification_unknown_tool():
    """未知验证工具应返回失败结果而非抛异常。"""
    v = Verifier(".")
    result = v.run_verification("nope")
    assert not result.passed
    assert "不支持的验证工具" in result.error_summary


@patch("qingxiaotuan.core.reflector.subprocess.run")
def test_run_verification_success(mock_run):
    """验证通过时 passed=True, exit_code=0。"""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    v = Verifier(".")
    result = v.run_verification("pytest")
    assert result.passed
    assert result.exit_code == 0
    assert result.error_summary == ""


@patch("qingxiaotuan.core.reflector.subprocess.run")
def test_run_verification_failure_extracts_summary(mock_run):
    """验证失败时 passed=False 且提取错误摘要。"""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="FAILED test_x.py::test_foo - AssertionError: 1 != 2")
    v = Verifier(".")
    result = v.run_verification("pytest")
    assert not result.passed
    assert result.exit_code == 1
    assert "AssertionError" in result.error_summary


@patch("qingxiaotuan.core.reflector.subprocess.run")
def test_run_verification_timeout(mock_run):
    """验证超时应返回友好错误。"""
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)
    v = Verifier(".", timeout=5)
    result = v.run_verification("pytest")
    assert not result.passed
    assert "超时" in result.error_summary


@patch("qingxiaotuan.core.reflector.subprocess.run")
def test_run_verification_exception(mock_run):
    """执行异常应被捕获并标记失败。"""
    mock_run.side_effect = OSError("no python")
    v = Verifier(".")
    result = v.run_verification("pytest")
    assert not result.passed
    assert "执行异常" in result.error_summary


# ------------------------------------------------------------------ Verifier: 错误摘要提取

def test_extract_error_summary_finds_keywords():
    output = "\n".join([
        "collecting ...",
        "tests/test_x.py:10: in test_foo",
        "AssertionError: expected 1, actual 2",
        "1 failed in 0.5s",
    ])
    summary = Verifier._extract_error_summary(output, "pytest")
    assert "AssertionError" in summary
    assert "failed" in summary


def test_extract_error_summary_fallback_last_lines():
    """无明确错误关键字时取最后几行。"""
    output = "line1\nline2\nline3\n"
    summary = Verifier._extract_error_summary(output, "pytest")
    assert "line3" in summary


# ------------------------------------------------------------------ Reflector: 决策流程

def test_reflect_all_passed_continue():
    """全部验证通过应返回 CONTINUE 并清零失败计数。"""
    r = Reflector(".")
    r.verifier = MagicMock()
    r.verifier.run_all.return_value = [
        VerificationResult(name="pytest", passed=True),
        VerificationResult(name="ruff", passed=True),
    ]
    result = r.reflect("修复 bug")
    assert result.decision == ReflectDecision.CONTINUE
    assert result.failure_count == 0
    assert "所有验证通过" in result.diagnosis
    assert r.failure_count == 0


def test_reflect_first_failure_fix():
    """第 1 次失败应返回 FIX 决策。"""
    r = Reflector(".")
    r.verifier = MagicMock()
    r.verifier.run_all.return_value = [
        VerificationResult(name="pytest", passed=False, output="AssertionError: 1 != 2"),
    ]
    result = r.reflect("修复 bug")
    assert result.decision == ReflectDecision.FIX
    assert result.failure_count == 1
    assert "断言失败" in result.diagnosis
    assert r.failure_count == 1


def test_reflect_second_failure_degrade():
    """第 2 次失败应返回 DEGRADE 并给出降级任务。"""
    r = Reflector(".")
    r.verifier = MagicMock()
    failed = [VerificationResult(name="pytest", passed=False, output="FAILED test_x.py::t")]
    r.verifier.run_all.return_value = failed

    r.reflect("修复测试失败")
    result = r.reflect("修复测试失败")
    assert result.decision == ReflectDecision.DEGRADE
    assert result.failure_count == 2
    assert result.degraded_task
    assert "降级" in result.summary


def test_reflect_third_failure_ask_user():
    """第 3 次失败应返回 ASK_USER。"""
    r = Reflector(".")
    r.verifier = MagicMock()
    failed = [VerificationResult(name="pytest", passed=False, output="FAILED")]
    r.verifier.run_all.return_value = failed

    for _ in range(2):
        r.reflect("任务")
    result = r.reflect("任务")
    assert result.decision == ReflectDecision.ASK_USER
    assert result.failure_count == 3
    assert "用户" in result.summary


def test_reflect_reset_clears_streak():
    """reset 应清零失败计数与历史。"""
    r = Reflector(".")
    r.verifier = MagicMock()
    r.verifier.run_all.return_value = [VerificationResult(name="pytest", passed=False)]
    r.reflect("任务")
    assert r.failure_count == 1
    r.reset()
    assert r.failure_count == 0
    assert r.history == []


def test_reflect_no_verifications_continue():
    """无可用验证工具时视为通过。"""
    r = Reflector(".")
    r.verifier = MagicMock()
    r.verifier.detect_available.return_value = []
    r.verifier.run_all.return_value = []
    result = r.reflect("任务")
    assert result.decision == ReflectDecision.CONTINUE


def test_reflect_on_verify_callback():
    """on_verify 回调应按每个验证工具触发。"""
    r = Reflector(".")
    r.verifier = MagicMock()
    r.verifier.run_all.return_value = [
        VerificationResult(name="pytest", passed=True),
        VerificationResult(name="ruff", passed=False),
    ]
    seen = []
    r.reflect("任务", on_verify=lambda name, passed: seen.append((name, passed)))
    assert seen == [("pytest", True), ("ruff", False)]


def test_reflect_history_appended():
    """每次 reflect 结果应追加到历史。"""
    r = Reflector(".")
    r.verifier = MagicMock()
    r.verifier.run_all.return_value = [VerificationResult(name="pytest", passed=True)]
    r.reflect("任务")
    r.reflect("任务")
    assert len(r.history) == 2


# ------------------------------------------------------------------ Reflector: 诊断

def test_diagnose_module_not_found():
    r = Reflector(".")
    failed = [VerificationResult(
        name="pytest", passed=False,
        output="ModuleNotFoundError: No module named 'requests'")]
    diagnosis = r._diagnose_failures(failed, "")
    assert "requests" in diagnosis
    assert "pip install" in diagnosis


def test_diagnose_import_error():
    r = Reflector(".")
    failed = [VerificationResult(
        name="pytest", passed=False,
        output="ImportError: cannot import name 'foo' from 'bar'")]
    diagnosis = r._diagnose_failures(failed, "")
    assert "foo" in diagnosis


def test_diagnose_assertion_error():
    r = Reflector(".")
    failed = [VerificationResult(
        name="pytest", passed=False, output="AssertionError: 1 != 2")]
    diagnosis = r._diagnose_failures(failed, "")
    assert "断言失败" in diagnosis


def test_diagnose_failed_test_names():
    r = Reflector(".")
    failed = [VerificationResult(
        name="pytest", passed=False,
        output="FAILED tests/test_a.py::test_1\nFAILED tests/test_b.py::test_2")]
    diagnosis = r._diagnose_failures(failed, "")
    assert "test_1" in diagnosis and "test_2" in diagnosis


def test_diagnose_lint_errors():
    r = Reflector(".")
    failed = [VerificationResult(
        name="ruff", passed=False,
        output="src/a.py:1:1: E401\nsrc/a.py:2:5: F841")]
    diagnosis = r._diagnose_failures(failed, "")
    assert "2 处" in diagnosis


def test_diagnose_npm_missing_module():
    r = Reflector(".")
    failed = [VerificationResult(
        name="npm_test", passed=False, output="Cannot find module 'lodash'")]
    diagnosis = r._diagnose_failures(failed, "")
    assert "npm install" in diagnosis


def test_diagnose_go_undefined():
    r = Reflector(".")
    failed = [VerificationResult(
        name="go_test", passed=False, output="undefined: Foo")]
    diagnosis = r._diagnose_failures(failed, "")
    assert "未定义" in diagnosis


def test_diagnose_unknown_failure():
    r = Reflector(".")
    failed = [VerificationResult(name="weird", passed=False, output="???")]
    diagnosis = r._diagnose_failures(failed, "")
    assert "无法自动识别" in diagnosis


def test_diagnose_uses_last_output():
    r = Reflector(".")
    failed = [VerificationResult(name="pytest", passed=False, output="FAILED")]
    diagnosis = r._diagnose_failures(failed, "Traceback ... Error ...")
    assert "异常堆栈" in diagnosis
    assert "错误信息" in diagnosis


# ------------------------------------------------------------------ Reflector: 建议与降级

def test_suggest_fix_pip_install():
    r = Reflector(".")
    failed = [VerificationResult(
        name="pytest", passed=False,
        output="ModuleNotFoundError: No module named 'pandas'")]
    fix = r._suggest_fix(failed, "")
    assert "pip install pandas" in fix


def test_suggest_fix_lint():
    r = Reflector(".")
    failed = [VerificationResult(name="ruff", passed=False, output="E401")]
    fix = r._suggest_fix(failed, "")
    assert "ruff check --fix" in fix


def test_suggest_fix_fallback():
    r = Reflector(".")
    failed = [VerificationResult(name="weird", passed=False, output="???")]
    fix = r._suggest_fix(failed, "")
    assert fix


def test_degrade_task_test_keyword():
    r = Reflector(".")
    assert "仅修复验证失败项" in r._degrade_task("修复测试失败", "诊断")


def test_degrade_task_build_keyword():
    r = Reflector(".")
    assert "仅修复编译错误" in r._degrade_task("修复编译错误", "诊断")


def test_degrade_task_general():
    r = Reflector(".")
    assert "缩小范围" in r._degrade_task("完成功能", "诊断")


# ------------------------------------------------------------------ Reflector: 反思提示

def test_build_reflection_prompt_continue():
    r = Reflector(".")
    from qingxiaotuan.core.reflector import ReflectResult
    res = ReflectResult(
        decision=ReflectDecision.CONTINUE,
        verifications=[VerificationResult(name="pytest", passed=True)],
        diagnosis="所有验证通过",
    )
    prompt = r.build_reflection_prompt("任务", res, "")
    assert "continue" in prompt
    assert "所有验证通过" in prompt


def test_build_reflection_prompt_ask_user():
    r = Reflector(".")
    from qingxiaotuan.core.reflector import ReflectResult
    res = ReflectResult(
        decision=ReflectDecision.ASK_USER,
        failure_count=3,
        diagnosis="无法解决",
    )
    prompt = r.build_reflection_prompt("任务", res, "")
    assert "请求帮助" in prompt or "用户" in prompt


def test_build_reflection_prompt_degrade():
    r = Reflector(".")
    from qingxiaotuan.core.reflector import ReflectResult
    res = ReflectResult(
        decision=ReflectDecision.DEGRADE,
        failure_count=2,
        diagnosis="诊断",
        degraded_task="仅修复验证失败项",
    )
    prompt = r.build_reflection_prompt("任务", res, "")
    assert "降级" in prompt
    assert "仅修复验证失败项" in prompt
