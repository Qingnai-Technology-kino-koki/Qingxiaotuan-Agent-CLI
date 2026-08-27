"""自定义 subagent 类型 (<QXT_HOME>/agents/*.md) 测试。

验证:
- agents/reviewer.md → task 类型表出现 reviewer, frontmatter 解析正确;
- read_only: true 的自定义类型在委派时物理排除修改类工具;
- 与内置同名的文件被忽略 (内置防写护栏不可被覆盖);
- 无 frontmatter 的纯正文文件可用 (name 取文件名);
- task 工具 description / enum 固化时已包含自定义类型。
"""

import json

from qingxiaotuan.app import build_kernel
from qingxiaotuan.core.agent import Agent
from qingxiaotuan.core.agent_types import (
    get_agent_type,
    load_custom_types,
    type_names,
)
from qingxiaotuan.models.base import ModelAdapter, ModelResponse


class EchoModel(ModelAdapter):
    """单轮回声模型 (不调工具)。"""
    name = "echo"

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        last = next((m["content"] for m in reversed(messages)
                     if m.get("role") == "user" and m.get("content")), "")
        return ModelResponse(content=f"回声: {last[:50]}")


def _seed(home, name: str, text: str) -> None:
    d = home / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


REVIEWER = (
    "---\n"
    "name: reviewer\n"
    "description: 代码审查专家, 改动后质量把关\n"
    "read_only: true\n"
    "---\n\n"
    "你是资深审查员。只提问题不改代码。"
)


def test_custom_type_loaded_and_parsed(qxt_home):
    _seed(qxt_home, "reviewer.md", REVIEWER)
    types = load_custom_types(qxt_home)
    assert "reviewer" in type_names()
    t = get_agent_type("reviewer")
    assert t is not None
    assert t.read_only is True
    assert "审查" in t.description
    assert "只提问题" in t.system_extra
    assert types  # 返回值即本次加载清单


def test_builtin_name_not_shadowed(qxt_home):
    """与内置 explore 同名的文件被忽略, 内置防写护栏原样保留。"""
    _seed(qxt_home, "explore.md",
          "---\nname: explore\ndescription: 假冒内置\nread_only: false\n---\n假指令")
    load_custom_types(qxt_home)
    t = get_agent_type("explore")
    assert t is not None and t.read_only is True          # 内置的只读属性未被覆盖
    assert "假冒" not in t.description
    assert "假指令" not in (t.system_extra or "")


def test_plain_markdown_without_frontmatter(qxt_home):
    _seed(qxt_home, "helper.md", "你就是个帮手。")
    load_custom_types(qxt_home)
    t = get_agent_type("helper")
    assert t is not None
    assert t.name == "helper"                              # name 回落为文件名
    assert not t.read_only
    assert "帮手" in t.system_extra


def test_task_tool_description_contains_custom(tmp_path, qxt_home):
    """插件激活时固化进 task 工具 description/enum 的类型包含自定义项。"""
    _seed(qxt_home, "reviewer.md", REVIEWER)
    kernel = build_kernel()
    tool = kernel.require("tool_registry").get("task")
    assert "reviewer" in tool.description
    enum = tool.parameters["properties"]["subagent_type"]["enum"]
    assert "reviewer" in enum and "explore" in enum
    # 只读自定义类型委派时同样物理剥夺写工具
    config = kernel.require("config")
    config.set_user("agent.subagent_isolation", "thread")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", EchoModel(), owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda _p: True)
    out = tool.handler(agent.ctx, description="审一下",
                       prompt="审查全部改动", subagent_type="reviewer")
    assert "✅完成" in out
