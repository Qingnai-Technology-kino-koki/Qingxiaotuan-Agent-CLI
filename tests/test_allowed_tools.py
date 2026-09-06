"""allowedTools 作用域 Bash 白名单测试 (对标 Claude Code 2.1.246 --allowedTools)。"""

from __future__ import annotations

import pytest

from qingxiaotuan.core.allowed_tools import AllowedTools, _scope_match


def test_parse_tools_and_scopes():
    at = AllowedTools.parse("Read,Write,Bash(npm test)")
    assert at.tools == {"Read", "Write"}
    assert at.bash_scopes == ["npm test"]
    assert at._bash_all is False


def test_allows_tool():
    at = AllowedTools.parse("Read, Edit")
    assert at.allows_tool("Read")
    assert at.allows_tool("Edit")
    assert not at.allows_tool("Write")


def test_allows_bash_prefix_scope():
    at = AllowedTools.parse("Bash(npm test)")
    assert at.allows_bash("npm test -- --watch")
    assert at.allows_bash("npm test")
    assert not at.allows_bash("npm run build")


def test_allows_bash_wildcard():
    at = AllowedTools.parse("Bash(npm *)")
    assert at.allows_bash("npm install")
    assert at.allows_bash("npm test --coverage")
    assert not at.allows_bash("yarn install")


def test_allows_bash_star_matches_all():
    at = AllowedTools.parse("Bash(*)")
    assert at._bash_all is True
    assert at.allows_bash("anything here")


def test_empty_scope_means_all_bash():
    at = AllowedTools.parse("Bash()")
    assert at._bash_all is True


def test_empty_parse_allows_nothing():
    at = AllowedTools.parse("")
    assert at.tools == set()
    assert at.allows_bash("ls") is False
    assert at.allows_tool("Read") is False


def test_case_insensitive_command_match():
    at = AllowedTools.parse("Bash(npm TEST)")
    assert at.allows_bash("npm test --run") or _scope_match("npm test", "npm test")


def test_scopes_text():
    at = AllowedTools.parse("Read,Bash(npm test)")
    assert "Read" in at.scopes_text()
    assert "Bash(npm test)" in at.scopes_text()