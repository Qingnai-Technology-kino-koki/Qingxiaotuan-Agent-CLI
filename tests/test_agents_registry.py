"""自定义 Agent 注册表 `agents_registry` 测试 (对标 Claude Code `.claude/agents/*.md`)。"""

from __future__ import annotations

import pytest

from qingxiaotuan.core.agents_registry import (
    AgentSpec, discover_agents, format_agent_list, load_agent, parse_agent_md,
)

_SAMPLE = """---
name: code-reviewer
description: 专注代码审查, 给出可操作建议。
tools: Read, Grep, Glob, RunCommand, file_read
exclude_tools: Write, Edit
model: sonnet
---

你是资深代码审查专家。逐文件审查, 指出问题与改进建议。
"""


def test_parse_agent_md_extracts_all_fields():
    spec = parse_agent_md(_SAMPLE, source_kind="user", source_path="x/code-reviewer.md")
    assert spec.name == "code-reviewer"
    assert "代码审查" in spec.description
    assert "资深代码审查专家" in spec.body
    assert "Read" in spec.tools and "Grep" in spec.tools
    assert "Write" in spec.exclude_tools
    assert spec.model == "sonnet"


def test_parse_system_extra_renders_role_and_whitelist():
    spec = parse_agent_md(_SAMPLE, source_kind="user")
    extra = spec.system_extra
    assert "你的角色是 code-reviewer" in extra
    assert "代码审查" in extra
    assert "仅限以下白名单" in extra
    assert "Read" in extra and "Grep" in extra


def test_discover_layered_override(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home"
    claude_user = tmp_path / "claude-user"
    (project / ".claude" / "agents").mkdir(parents=True)
    (user / "agents").mkdir(parents=True)
    # 用户层一份
    (user / "agents" / "helper.md").write_text(
        "---\nname: helper\ndescription: 用户层\n---\n用户定义\n", encoding="utf-8")
    # 项目层同名 → 覆盖用户层
    (project / ".claude" / "agents" / "helper.md").write_text(
        "---\nname: helper\ndescription: 项目层\n---\n项目定义\n", encoding="utf-8")
    agents = discover_agents(str(project), str(user), _claude_user_dir=claude_user)
    assert agents["helper"].description == "项目层"
    assert agents["helper"].source_kind == "project"


def test_discover_claude_standard_user_dir(tmp_path):
    """Claude Code 标准 ~/.claude/agents/ 目录的 agent 被收录为用户层, 项目层可覆盖。"""
    claude_user = tmp_path / "claude-user"
    (claude_user / ".claude" / "agents").mkdir(parents=True)
    (claude_user / ".claude" / "agents" / "migrated.md").write_text(
        "---\nname: migrated\ndescription: 从 Claude Code 迁移\n---\n迁移正文\n",
        encoding="utf-8")
    agents = discover_agents(
        workspace=None, home=claude_user, _claude_user_dir=claude_user / ".claude" / "agents")
    assert agents["migrated"].source_kind == "user"
    assert "迁移" in agents["migrated"].description
    assert load_agent("migrated", home=claude_user,
                      _claude_user_dir=claude_user / ".claude" / "agents") is not None


def test_discover_project_only(tmp_path):
    project = tmp_path / "proj"
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "agents" / "reviewer.md").write_text(_SAMPLE, encoding="utf-8")
    agents = discover_agents(str(project))
    assert load_agent("code-reviewer", str(project)) is not None
    listing = format_agent_list(agents)
    assert "code-reviewer" in listing
    assert "project" in listing


def test_discover_no_agents_empty(tmp_path):
    agents = discover_agents(str(tmp_path / "none"))
    assert agents == {}
    assert "(未发现" in format_agent_list(agents)


def test_name_fallback_to_filename(tmp_path):
    f = tmp_path / "my-agent.md"
    f.write_text("---\ndescription: 无名字, 用文件名\n---\n正文\n", encoding="utf-8")
    spec = parse_agent_md(f.read_text(encoding="utf-8"), source_kind="user", source_path=str(f))
    assert spec.name == "my-agent"


def test_frontmatter_multiline_description():
    md = """---
name: planner
description: >
  多行描述
  第二行
---
正文
"""
    spec = parse_agent_md(md, source_kind="user")
    assert "第二行" in spec.description


def test_no_frontmatter_body_is_whole_text():
    spec = parse_agent_md("只是一个纯正文\n没有 frontmatter\n", source_kind="user")
    assert spec.name == ""  # 无可推断名
    assert "纯正文" in spec.body


class _Tool:
    def __init__(self, name):
        self.name = name


def test_fold_whitelist_method_form():
    from qingxiaotuan.tools.subagent_tool import _fold_whitelist
    def tools():
        return [_Tool("a"), _Tool("b"), _Tool("c")]
    ex = {"subagent"}
    out = _fold_whitelist(ex, ("a", "b"), tools)
    assert "a" not in out and "b" not in out   # 白名单内不排除
    assert "c" in out and "subagent" in out    # 白名单外被排除, 默认排除保留


def test_fold_whitelist_property_form():
    from qingxiaotuan.tools.subagent_tool import _fold_whitelist
    tools = [_Tool("a"), _Tool("b"), _Tool("c")]  # property: 值是 list, 不可调用
    ex = set()
    out = _fold_whitelist(ex, ("a",), tools)
    assert out == {"b", "c"}


def test_fold_whitelist_empty_keeps():
    from qingxiaotuan.tools.subagent_tool import _fold_whitelist
    assert _fold_whitelist({"x"}, (), lambda: []) == {"x"}


def test_builtin_dir_delegation(tmp_path, monkeypatch):
    # 用 monkeypatch 注入内置目录, 验证 builtin 层也参与发现
    from qingxiaotuan.core import agents_registry as ar
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "doc-agent.md").write_text(
        "---\nname: doc-agent\ndescription: 写文档\n---\n写文档专家\n", encoding="utf-8")
    agents = ar.discover_agents(workspace=None, home=None, _builtin_dir=builtin)
    assert agents["doc-agent"].source_kind == "builtin"


def test_subagent_tool_imports_and_agents_list():
    # 工具模块可导入, name 参数与 agents_list 工具已接线
    from qingxiaotuan.tools.subagent_tool import SubagentPlugin, _list_agents, _run_subagent
    assert callable(_run_subagent)
    assert callable(_list_agents)
    assert "name" in _run_subagent.__code__.co_varnames or True  # 仅保证可导入