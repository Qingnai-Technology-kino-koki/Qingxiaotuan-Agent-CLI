"""Tests for Universal Skill Format Adapter, Plugin SDK, and Skill Market v2."""

import json
import os
import shutil
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

from qingxiaotuan.skills.universal_format import (
    SkillPackage,
    SkillMdAdapter,
    CursorRulesAdapter,
    ClaudeMdAdapter,
    AgentsMdAdapter,
    WindsurfRulesAdapter,
    CopilotInstructionsAdapter,
    ClineRulesAdapter,
    CursorRulesDirAdapter,
    GenericFrontmatterAdapter,
    detect_format,
    parse_skill_file,
    parse_skill_directory,
    import_from_agent_project,
    batch_import,
    FORMAT_INFO,
)
from qingxiaotuan.skills.plugin_sdk import (
    PluginManifest,
    PluginManager,
    HeadersHelper,
    _parse_version,
    _version_satisfies,
)


# ================================================================ SkillPackage

class TestSkillPackage:
    def test_to_skill_md_basic(self):
        pkg = SkillPackage(name="test-skill", description="A test skill", body="# Hello\nWorld")
        md = pkg.to_skill_md()
        assert "---" in md
        assert "name: test-skill" in md
        assert "description: A test skill" in md
        assert "# Hello" in md
        assert "World" in md

    def test_to_skill_md_sanitizes_name(self):
        pkg = SkillPackage(name="My Skill! @#$", description="test", body="body")
        md = pkg.to_skill_md()
        assert "name: my-skill" in md

    def test_to_skill_md_with_metadata(self):
        pkg = SkillPackage(
            name="meta-skill",
            description="test",
            body="body",
            metadata={"author": "test", "version": "1.0"},
        )
        md = pkg.to_skill_md()
        assert "metadata:" in md
        assert "author: test" in md

    def test_to_skill_md_with_tags(self):
        pkg = SkillPackage(name="tagged", description="test", body="body", tags=["python", "testing"])
        md = pkg.to_skill_md()
        assert "tags: python, testing" in md


# ================================================================ Format Detection

class TestFormatDetection:
    def test_detect_skill_md(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\nBody")
        assert detect_format(skill_dir / "SKILL.md") == "skill_md"

    def test_detect_cursorrules(self, tmp_path):
        (tmp_path / ".cursorrules").write_text("Rules here")
        assert detect_format(tmp_path / ".cursorrules") == "cursorrules"

    def test_detect_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Instructions\nDo stuff")
        assert detect_format(tmp_path / "CLAUDE.md") == "claude_md"

    def test_detect_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Agent Instructions")
        assert detect_format(tmp_path / "AGENTS.md") == "agents_md"

    def test_detect_windsurfrules(self, tmp_path):
        (tmp_path / ".windsurfrules").write_text("Windsurf rules")
        assert detect_format(tmp_path / ".windsurfrules") == "windsurfrules"

    def test_detect_clinerules(self, tmp_path):
        (tmp_path / ".clinerules").write_text("Cline rules")
        assert detect_format(tmp_path / ".clinerules") == "clinerules"

    def test_detect_unknown(self, tmp_path):
        (tmp_path / "random.txt").write_text("hello")
        assert detect_format(tmp_path / "random.txt") == "unknown"


# ================================================================ SKILL.md Adapter

class TestSkillMdAdapter:
    def test_parse_skill_md(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(dedent("""\
            ---
            name: test-skill
            description: A test skill for testing
            version: 1.0.0
            license: MIT
            ---
            # Instructions
            Do the thing.
        """))
        adapter = SkillMdAdapter()
        pkg = adapter.parse(skill_dir / "SKILL.md")
        assert pkg is not None
        assert pkg.name == "test-skill"
        assert pkg.description == "A test skill for testing"
        assert pkg.version == "1.0.0"
        assert pkg.license == "MIT"
        assert "# Instructions" in pkg.body

    def test_parse_non_skill_md(self, tmp_path):
        (tmp_path / "README.md").write_text("# Not a skill")
        adapter = SkillMdAdapter()
        assert adapter.parse(tmp_path / "README.md") is None


# ================================================================ Cursor Rules Adapter

class TestCursorRulesAdapter:
    def test_parse_cursorrules(self, tmp_path):
        (tmp_path / ".cursorrules").write_text("# My Rules\nAlways use type hints.")
        adapter = CursorRulesAdapter()
        pkg = adapter.parse(tmp_path / ".cursorrules")
        assert pkg is not None
        assert pkg.source_format == "cursorrules"
        assert "Always use type hints" in pkg.body

    def test_parse_non_cursorrules(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc")
        adapter = CursorRulesAdapter()
        assert adapter.parse(tmp_path / ".gitignore") is None


# ================================================================ Claude.md Adapter

class TestClaudeMdAdapter:
    def test_parse_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Project Instructions\nUse pytest for tests.")
        adapter = ClaudeMdAdapter()
        pkg = adapter.parse(tmp_path / "CLAUDE.md")
        assert pkg is not None
        assert pkg.source_format == "claude_md"
        assert "pytest" in pkg.body

    def test_case_insensitive(self, tmp_path):
        (tmp_path / "claude.md").write_text("# Instructions")
        adapter = ClaudeMdAdapter()
        pkg = adapter.parse(tmp_path / "claude.md")
        assert pkg is not None


# ================================================================ Agents.md Adapter

class TestAgentsMdAdapter:
    def test_parse_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Vercel Agents\nUse server components.")
        adapter = AgentsMdAdapter()
        pkg = adapter.parse(tmp_path / "AGENTS.md")
        assert pkg is not None
        assert pkg.source_format == "agents_md"


# ================================================================ Windsurf Rules Adapter

class TestWindsurfRulesAdapter:
    def test_parse_windsurfrules(self, tmp_path):
        (tmp_path / ".windsurfrules").write_text("# Windsurf\nAlways format code.")
        adapter = WindsurfRulesAdapter()
        pkg = adapter.parse(tmp_path / ".windsurfrules")
        assert pkg is not None
        assert pkg.source_format == "windsurfrules"


# ================================================================ Copilot Instructions Adapter

class TestCopilotInstructionsAdapter:
    def test_parse_copilot_instructions(self, tmp_path):
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "copilot-instructions.md").write_text("# Copilot\nUse TypeScript.")
        adapter = CopilotInstructionsAdapter()
        pkg = adapter.parse(github_dir / "copilot-instructions.md")
        assert pkg is not None
        assert pkg.source_format == "copilot_instructions"


# ================================================================ Cursor Rules Dir Adapter

class TestCursorRulesDirAdapter:
    def test_parse_rules_dir(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "testing.md").write_text("---\nname: testing\n---\n# Testing Rules\nUse TDD.")
        adapter = CursorRulesDirAdapter()
        pkg = adapter.parse(rules_dir / "testing.md")
        assert pkg is not None
        assert pkg.source_format == "cursor_rules_dir"


# ================================================================ Generic Frontmatter Adapter

class TestGenericFrontmatterAdapter:
    def test_parse_generic_frontmatter(self, tmp_path):
        (tmp_path / "custom.md").write_text("---\nname: custom\ndescription: A custom skill\n---\n# Custom\nDo custom things.")
        adapter = GenericFrontmatterAdapter()
        pkg = adapter.parse(tmp_path / "custom.md")
        assert pkg is not None
        assert pkg.name == "custom"

    def test_no_frontmatter_returns_none(self, tmp_path):
        (tmp_path / "plain.md").write_text("# Just a markdown file")
        adapter = GenericFrontmatterAdapter()
        assert adapter.parse(tmp_path / "plain.md") is None


# ================================================================ parse_skill_file

class TestParseSkillFile:
    def test_auto_detect_skill_md(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\nBody")
        pkg = parse_skill_file(skill_dir / "SKILL.md")
        assert pkg is not None
        assert pkg.name == "my-skill"

    def test_auto_detect_cursorrules(self, tmp_path):
        (tmp_path / ".cursorrules").write_text("Rules here")
        pkg = parse_skill_file(tmp_path / ".cursorrules")
        assert pkg is not None
        assert pkg.source_format == "cursorrules"


# ================================================================ parse_skill_directory

class TestParseSkillDirectory:
    def test_directory_with_skill_md(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\nBody")
        pkgs = parse_skill_directory(skill_dir)
        assert len(pkgs) == 1
        assert pkgs[0].name == "test"

    def test_directory_with_cursorrules(self, tmp_path):
        (tmp_path / ".cursorrules").write_text("# Rules")
        pkgs = parse_skill_directory(tmp_path)
        assert len(pkgs) == 1

    def test_directory_with_multiple_formats(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Claude Instructions")
        (tmp_path / ".windsurfrules").write_text("# Windsurf Rules")
        pkgs = parse_skill_directory(tmp_path)
        assert len(pkgs) == 2

    def test_empty_directory(self, tmp_path):
        pkgs = parse_skill_directory(tmp_path)
        assert len(pkgs) == 0


# ================================================================ import_from_agent_project

class TestImportFromAgentProject:
    def test_import_cursor_project(self, tmp_path):
        project = tmp_path / "cursor-project"
        project.mkdir()
        (project / ".cursorrules").write_text("# Cursor Rules\nAlways use semicolons.")
        rules_dir = project / "rules"
        rules_dir.mkdir()
        (rules_dir / "testing.md").write_text("---\nname: testing\ndescription: Testing rules\n---\nUse TDD.")
        pkgs = import_from_agent_project(project)
        assert len(pkgs) >= 2

    def test_import_claude_project(self, tmp_path):
        project = tmp_path / "claude-project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("# CLAUDE.md\nUse pytest.")
        pkgs = import_from_agent_project(project)
        assert len(pkgs) >= 1

    def test_import_nonexistent(self, tmp_path):
        pkgs = import_from_agent_project(tmp_path / "nonexistent")
        assert len(pkgs) == 0


# ================================================================ batch_import

class TestBatchImport:
    def test_batch_import_files(self, tmp_path):
        source = tmp_path / "source"
        source.mkdir()
        (source / ".cursorrules").write_text("# Rules")
        output = tmp_path / "output"
        results = batch_import([source], output)
        assert len(results) >= 1
        # 检查输出目录
        for name, path in results.items():
            assert Path(path).exists()

    def test_batch_import_no_overwrite(self, tmp_path):
        source = tmp_path / "source"
        source.mkdir()
        (source / ".cursorrules").write_text("# Rules")
        output = tmp_path / "output"
        batch_import([source], output)
        # 再次导入不覆盖 (同名文件已存在)
        results = batch_import([source], output, overwrite=False)
        assert len(results) == 0  # 已存在, 跳过


# ================================================================ PluginManifest

class TestPluginManifest:
    def test_from_dict(self):
        data = {
            "name": "test-plugin",
            "version": "1.0.0",
            "description": "A test plugin",
            "skills": [{"name": "skill1", "description": "Test skill"}],
        }
        manifest = PluginManifest.from_dict(data)
        assert manifest.name == "test-plugin"
        assert manifest.version == "1.0.0"
        assert len(manifest.skills) == 1

    def test_to_dict(self):
        manifest = PluginManifest(name="test", version="0.1.0")
        d = manifest.to_dict()
        assert d["name"] == "test"
        assert d["version"] == "0.1.0"

    def test_from_dict_minimal(self):
        manifest = PluginManifest.from_dict({"name": "minimal"})
        assert manifest.name == "minimal"
        assert manifest.version == "0.1.0"
        assert manifest.skills == []


# ================================================================ PluginManager

class TestPluginManager:
    def test_install_plugin(self, tmp_path):
        # 创建插件源
        plugin_dir = tmp_path / "source" / "my-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "my-plugin",
            "version": "1.0.0",
            "description": "Test plugin",
            "skills": [{"name": "test-skill", "description": "A test skill"}],
        }))
        (plugin_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: test\n---\nBody")

        pm = PluginManager(tmp_path / "plugins")
        info = pm.install_from_directory(plugin_dir)
        assert info.manifest.name == "my-plugin"
        assert len(info.skill_names()) == 1
        assert "my-plugin:test-skill" in info.skill_names()

    def test_enable_disable(self, tmp_path):
        plugin_dir = tmp_path / "source" / "my-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(json.dumps({"name": "my-plugin"}))

        pm = PluginManager(tmp_path / "plugins")
        pm.install_from_directory(plugin_dir)
        assert pm.get_plugin("my-plugin").enabled is True

        pm.disable("my-plugin")
        assert pm.get_plugin("my-plugin").enabled is False

        pm.enable("my-plugin")
        assert pm.get_plugin("my-plugin").enabled is True

    def test_uninstall(self, tmp_path):
        plugin_dir = tmp_path / "source" / "my-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(json.dumps({"name": "my-plugin"}))

        pm = PluginManager(tmp_path / "plugins")
        pm.install_from_directory(plugin_dir)
        assert pm.get_plugin("my-plugin") is not None

        pm.uninstall("my-plugin")
        assert pm.get_plugin("my-plugin") is None

    def test_list_plugins(self, tmp_path):
        for i in range(3):
            plugin_dir = tmp_path / "source" / f"plugin-{i}"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "plugin.json").write_text(json.dumps({"name": f"plugin-{i}"}))

        pm = PluginManager(tmp_path / "plugins")
        for i in range(3):
            pm.install_from_directory(tmp_path / "source" / f"plugin-{i}")

        plugins = pm.list_plugins()
        assert len(plugins) == 3

    def test_get_all_skills(self, tmp_path):
        plugin_dir = tmp_path / "source" / "my-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "my-plugin",
            "skills": [
                {"name": "skill-a", "description": "Skill A"},
                {"name": "skill-b", "description": "Skill B"},
            ],
        }))
        (plugin_dir / "SKILL.md").write_text("---\nname: skill-a\n---\nBody A")

        pm = PluginManager(tmp_path / "plugins")
        pm.install_from_directory(plugin_dir)
        skills = pm.get_all_skills()
        assert len(skills) == 2
        assert any("skill-a" in s["name"] for s in skills)


# ================================================================ Semver

class TestSemver:
    def test_parse_version(self):
        assert _parse_version("1.2.3") == (1, 2, 3)
        assert _parse_version("0.1.0") == (0, 1, 0)
        assert _parse_version("invalid") == (0, 0, 0)

    def test_version_satisfies_exact(self):
        assert _version_satisfies("1.2.3", "1.2.3")
        assert not _version_satisfies("1.2.3", "1.2.4")

    def test_version_satisfies_caret(self):
        assert _version_satisfies("1.2.3", "^1.2.3")
        assert _version_satisfies("1.3.0", "^1.2.3")
        assert not _version_satisfies("2.0.0", "^1.2.3")

    def test_version_satisfies_tilde(self):
        assert _version_satisfies("1.2.3", "~1.2.3")
        assert _version_satisfies("1.2.5", "~1.2.3")
        assert not _version_satisfies("1.3.0", "~1.2.3")

    def test_version_satisfies_gte(self):
        assert _version_satisfies("1.3.0", ">=1.2.3")
        assert not _version_satisfies("1.2.2", ">=1.2.3")


# ================================================================ HeadersHelper

class TestHeadersHelper:
    def test_resolve_empty_command(self):
        assert HeadersHelper.resolve("") == {}

    def test_resolve_invalid_command(self):
        assert HeadersHelper.resolve("nonexistent_command_12345") == {}
