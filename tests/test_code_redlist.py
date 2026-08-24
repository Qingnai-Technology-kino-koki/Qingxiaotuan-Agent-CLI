"""验证 code 工具的危险命令红名单拦截。"""

from qingxiaotuan.tools.code import _looks_dangerous


def test_rm_rf_blocked():
    assert _looks_dangerous("rm -rf /tmp/foo") is True
    assert _looks_dangerous("rm -fr build") is True


def test_git_push_force_blocked():
    assert _looks_dangerous("git push --force origin main") is True
    assert _looks_dangerous("git push -f") is True


def test_git_reset_hard_blocked():
    assert _looks_dangerous("git reset --hard HEAD~3") is True


def test_git_clean_blocked():
    assert _looks_dangerous("git clean -fd") is True


def test_sudo_blocked():
    assert _looks_dangerous("sudo rm file") is True


def test_fork_bomb_blocked():
    assert _looks_dangerous(":(){ :|:& };:") is True


def test_safe_commands_allowed():
    # 这些在只读/自测工具里应放行
    assert _looks_dangerous("pytest -q") is False
    assert _looks_dangerous("git status --short") is False
    assert _looks_dangerous("git diff --stat") is False
    assert _looks_dangerous("python -m pytest tests/") is False
    assert _looks_dangerous("npm test") is False
    assert _looks_dangerous("cargo test") is False
