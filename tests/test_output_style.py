"""测试: 输出风格 (Output Styles) —— 对标 Claude Code 2.1.237 的内置/自定义输出风格。

覆盖:
- default 时 system 提示逐字节不变 (prompt cache 硬约束)
- 内置 concise / explanatory / learning 风格注入
- 自定义风格文件 (路径 / 工作区 .qxt/output-style.md) 注入
- 未知风格安全忽略
- Agent._build_system 从 ui.output_style 读取配置
"""

from __future__ import annotations

from pathlib import Path

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.core.prompts import build_system_prompt, resolve_output_style


class _MockModel:
    name = "mock"

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        return None


def _prompt(home: Path, workspace: str, **kw) -> str:
    return build_system_prompt(home=home, workspace=workspace, **kw)


# ----------------------------------------------------------------- default 不注入

def test_default_style_keeps_prompt_byte_identical(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    base = _prompt(home, str(tmp_path))
    assert _prompt(home, str(tmp_path), output_style="") == base
    assert _prompt(home, str(tmp_path), output_style="default") == base
    assert "输出风格" not in base


# ----------------------------------------------------------------- 内置风格

def test_builtin_styles_injected(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    for style, marker in (("concise", "尽可能少"),
                          ("explanatory", "设计取舍"),
                          ("learning", "TODO(human)")):
        text = _prompt(home, str(tmp_path), output_style=style)
        assert "## 输出风格" in text
        assert marker in text
        # 风格段在准则之后追加 (保持其他段落次序稳定)
        assert text.index("准则:") < text.index("## 输出风格")


def test_resolve_output_style_case_insensitive():
    assert resolve_output_style("Concise", "") == resolve_output_style("concise", "")
    assert resolve_output_style("DEFAULT", "") == ""


# ----------------------------------------------------------------- 自定义风格

def test_custom_style_file_by_name_or_path(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".qxt").mkdir(parents=True)
    (ws / "my-style.md").write_text("# 只输出 diff", encoding="utf-8")
    assert resolve_output_style("my-style.md", str(ws)) == "# 只输出 diff"

    (ws / ".qxt" / "output-style.md").write_text("工作区级自定义风格", encoding="utf-8")
    assert resolve_output_style("custom", str(ws)) == "工作区级自定义风格"


def test_unknown_style_ignored(tmp_path):
    assert resolve_output_style("no-such-style", str(tmp_path)) == ""
    home = tmp_path / "home"
    home.mkdir()
    assert "输出风格" not in _prompt(home, str(tmp_path), output_style="no-such-style")


# ----------------------------------------------------------------- Agent 接线

def test_agent_reads_output_style_from_config(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    config.data["ui"]["output_style"] = "concise"
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", _MockModel(), owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda _p: True)
    system = agent._build_system()
    assert "## 输出风格" in system and "尽可能少" in system
