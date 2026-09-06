"""安全引擎降误杀: 良性开发命令不应被升级为 high/critical 而遭拦截/反复确认。

覆盖 Task 2 的「重点降低安全引擎误杀率」:
- 文件读写 / git 常规操作 / 包管理 / 测试运行 / lint 等常见命令不得被拦截;
- 但仍保留对不可逆破坏 (rm -rf /、force push、dd、mkfs) 与系统关键路径写入的拦截。
"""

from __future__ import annotations

from qingxiaotuan.core.whitelist import get_warning_level
from qingxiaotuan.ext.safety_engine import (
    SafetyEngine,
    is_benign_dev_command,
    is_hard_redline,
)


# ------------------------------------------------------------ 良性开发命令
_BENIGN_COMMANDS = [
    "ls -la src",
    "cat README.md",
    "head -n 20 main.py",
    "tail -f app.log",
    "grep -rn 'def ' src",
    "find . -name '*.py'",
    "git status",
    "git diff --stat",
    "git log --oneline -10",
    "git branch",
    "git fetch origin",
    "git pull",
    "git add .",
    "git commit -m 'chore: x'",
    "git clone https://example.com/repo.git",
    "pip install requests",
    "pip3 install -r requirements.txt",
    "npm install",
    "npm ci",
    "npm run build",
    "pnpm add lodash",
    "yarn install",
    "poetry add flask",
    "uv pip install numpy",
    "cargo build",
    "go build ./...",
    "pytest tests/",
    "python -m pytest -q",
    "python -m unittest discover",
    "ruff check .",
    "black --check .",
    "isort --check-only .",
    "mypy src/",
    "flake8 qingxiaotuan",
    "eslint src/",
    "tsc --noEmit",
    "echo hello",
    "mkdir -p build",
    "cp a.txt b.txt",
    "mv old new",
    "touch file.txt",
    "tee out.log < input.txt",
]


def test_benign_commands_recognized():
    for cmd in _BENIGN_COMMANDS:
        assert is_benign_dev_command(cmd), f"应识别为良性命令: {cmd!r}"


def test_benign_score_is_none():
    eng = SafetyEngine()
    for cmd in _BENIGN_COMMANDS:
        res = eng.score({"command": cmd, "type": "shell"})
        assert res["risk"] == "none", f"良性命令不应是风险级: {cmd!r} -> {res}"
        assert res["block"] is False
        assert res["reasons"] == []


def test_benign_warning_level_is_zero():
    for cmd in _BENIGN_COMMANDS:
        assert get_warning_level(cmd) == 0, f"良性命令不应触发确认: {cmd!r}"


# ------------------------------------------------------------ 系统关键路径仍被拦截
def test_system_path_write_not_benign():
    # 写系统关键路径的命令不被视为"良性", 因此不会被 score() 降级为 none
    assert not is_benign_dev_command("mv payload /etc/cron.d/x")
    assert not is_benign_dev_command("echo x > /etc/foo")
    assert not is_benign_dev_command("mv payload /usr/lib/")


def test_system_path_still_high_or_critical():
    # 现有模式 (mv /etc/、> /etc/) 仍能命中 medium, 证明良性降级不会压制真实风险
    eng = SafetyEngine()
    res = eng.score({"command": "mv payload /etc/cron.d/x", "type": "shell"})
    assert res["risk"] in ("critical", "high", "medium")
    res2 = eng.score({"command": "echo x > /etc/foo", "type": "shell"})
    assert res2["risk"] in ("critical", "high", "medium")


# ------------------------------------------------------------ 不可逆破坏仍被拦截
def test_destructive_not_benign():
    destructive = [
        "rm -rf /",
        "rm -rf ./build",
        "git push --force",
        "git push origin main --force",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sdb1",
        "chmod -R 000 /",
    ]
    for cmd in destructive:
        assert not is_benign_dev_command(cmd), f"不可逆操作不应判为良性: {cmd!r}"


def test_destructive_hard_redline_holds():
    assert is_hard_redline("rm -rf /")
    assert is_hard_redline("rm -rf ./build")
    assert is_hard_redline("git push --force")
    assert is_hard_redline("dd if=/dev/zero of=/dev/sda")


# ------------------------------------------------------------ 跨 workspace / 引号路径
def test_benign_with_quoted_spaces():
    # 含空格路径不应破坏良性判定 (修复前的 _expand_globs 会拆碎它)
    assert is_benign_dev_command('ls "my project/src"')
    assert is_benign_dev_command("cat 'a b.txt'")
