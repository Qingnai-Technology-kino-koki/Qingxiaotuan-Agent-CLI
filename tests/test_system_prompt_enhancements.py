"""测试 system prompt 的新增段落: Windows 外骨骼 / 上网查证 / 代码开发 / 安全强制。"""
import platform
import sys
from pathlib import Path

import pytest


class TestSystemPromptWindowsGuidance:
    """Windows 命令外骨骼指引。"""

    def test_windows_section_present_on_win32(self, monkeypatch):
        """在 Windows 平台上, system prompt 应包含 Windows 命令外骨骼。"""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        assert "Windows 命令外骨骼" in prompt
        assert "PowerShell" in prompt
        assert "windows-cmd-powershell" in prompt

    def test_windows_section_absent_on_linux(self, monkeypatch):
        """在 Linux 平台上, system prompt 不应包含 Windows 外骨骼。"""
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        assert "Windows 命令外骨骼" not in prompt

    def test_windows_guidance_mentions_key_practices(self, monkeypatch):
        """Windows 指引应包含关键最佳实践。"""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        # 应包含关键 Windows 建议
        assert "encoding" in prompt.lower() or "utf-8" in prompt
        assert "pathlib" in prompt or "正斜杠" in prompt
        assert "python -m" in prompt


class TestSystemPromptWebGuidance:
    """上网查证能力指引。"""

    def test_web_guidance_always_present(self):
        """上网查证指引应在所有平台上注入。"""
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        assert "上网查证" in prompt
        assert "web_search" in prompt
        assert "web_fetch" in prompt

    def test_web_guidance_mentions_citation(self):
        """指引应要求模型在回答中注明引用来源。"""
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        assert "URL" in prompt or "url" in prompt
        assert "注明" in prompt or "引用" in prompt


class TestSystemPromptCodeDevGuidance:
    """代码开发能力建设指引。"""

    def test_code_dev_guidance_present(self):
        """代码开发指引应始终注入。"""
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        assert "代码开发能力" in prompt
        assert "类型注解" in prompt
        assert "run_tests" in prompt

    def test_code_dev_guidance_mentions_review(self):
        """代码开发指引应提到代码审查。"""
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        assert "代码审查" in prompt or "自查" in prompt
        assert "bare except" in prompt or "异常处理" in prompt


class TestSystemPromptSandboxGuidance:
    """安全强制指引。"""

    def test_sandbox_guidance_present(self):
        """安全强制指引应始终注入。"""
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        assert "安全强制" in prompt
        assert "确认" in prompt
        assert "注入" in prompt

    def test_sandbox_guidance_mentions_dangerous_ops(self):
        """安全指引应提到危险操作。"""
        from qingxiaotuan.core.prompts import build_system_prompt
        prompt = build_system_prompt(
            home=Path("/tmp/test-home"),
            workspace="/tmp/test-ws",
            reply_language="zh-CN",
        )
        assert "删除文件" in prompt or "破坏性" in prompt
        assert "沙箱" in prompt


class TestWindowsSkillFile:
    """Windows 命令参考 Skill 文件。"""

    def test_skill_file_exists(self):
        """Windows 命令参考 skill 文件应存在。"""
        skill_path = (
            Path(__file__).resolve().parent.parent
            / "qingxiaotuan"
            / "resources"
            / "skills"
            / "builtin"
            / "windows-cmd-powershell.md"
        )
        assert skill_path.exists(), f"Skill file not found: {skill_path}"

    def test_skill_file_has_frontmatter(self):
        """Skill 文件应有 YAML frontmatter。"""
        skill_path = (
            Path(__file__).resolve().parent.parent
            / "qingxiaotuan"
            / "resources"
            / "skills"
            / "builtin"
            / "windows-cmd-powershell.md"
        )
        content = skill_path.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "name:" in content
        assert "description:" in content

    def test_skill_file_covers_key_commands(self):
        """Skill 文件应覆盖关键的 Windows 命令。"""
        skill_path = (
            Path(__file__).resolve().parent.parent
            / "qingxiaotuan"
            / "resources"
            / "skills"
            / "builtin"
            / "windows-cmd-powershell.md"
        )
        content = skill_path.read_text(encoding="utf-8")
        # 应覆盖关键命令
        assert "Get-ChildItem" in content  # PowerShell ls
        assert "Remove-Item" in content    # PowerShell rm
        assert "Copy-Item" in content      # PowerShell cp
        assert "Invoke-WebRequest" in content  # PowerShell curl
        assert "Compress-Archive" in content   # PowerShell zip
        assert "winget" in content         # Windows 包管理

    def test_skill_file_covers_pitfalls(self):
        """Skill 文件应覆盖常见陷阱。"""
        skill_path = (
            Path(__file__).resolve().parent.parent
            / "qingxiaotuan"
            / "resources"
            / "skills"
            / "builtin"
            / "windows-cmd-powershell.md"
        )
        content = skill_path.read_text(encoding="utf-8")
        assert "路径分隔符" in content
        assert "编码" in content
        assert "换行符" in content
        assert "260" in content or "MAX_PATH" in content
