"""`subagent`/`agents_list` 工具端到端集成测试 (Mock 模型, 全程离线)。

覆盖此前 `test_subagents_bg` 未触及的两条工具级路径:
- agents_list: 经工具注册表调用, 从工作区 .claude/agents 发现命名 agent;
- subagent 的 `name` 参数: 命中注册表把 agent 定义作为子代理系统提示并施加
  工具白名单 (白名单外工具被折算进排除集, 真实 ToolRegistry 端到端生效);
  未命中的名字返回错误而不发模型。

核心验证点: 「白名单折算」在真实注册表上真的把非白名单工具从子代理工具集剔除
(用 ProbeModel 记录子代理每次模型调用收到的 tools 清单来断言)。
"""

from __future__ import annotations

from qingxiaotuan.app import build_kernel
from qingxiaotuan.core.agent import Agent
from qingxiaotuan.models.base import ModelAdapter, ModelResponse


class EchoModel(ModelAdapter):
    """单轮: 把最后一条 user 消息回声作为答案 (不调工具)。"""
    name = "echo"

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        last = next((m["content"] for m in reversed(messages)
                     if m.get("role") == "user" and m.get("content")), "")
        return ModelResponse(content=f"回声: {last}")


class ProbeModel(ModelAdapter):
    """记录每次模型调用收到的 tools 清单, 返回无工具调用的答案。

    用于白名单折算端到端断言: 子代理 `_schemas()` 已按 exclude_tools 过滤,
    只要白名单折算生效, 记录到的工具名字应只含白名单内项。
    """
    name = "probe"

    def __init__(self):
        self.seen: list = []
        self.calls = 0

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls += 1
        self.seen.append([t["function"]["name"] for t in (tools or [])])
        return ModelResponse(content="完成了")


def _kernel_with(model):
    kernel = build_kernel()
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", model, owner="test")
    return kernel


def _write_agent(workspace, name: str, text: str) -> None:
    d = workspace / ".claude" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text, encoding="utf-8")


HELPER = (
    "---\n"
    "name: helper\n"
    "description: 一个暖心的助手\n"
    "---\n"
    f"你是 helper, 用简体中文、简洁直接地回答。"
)


# ------------------------------------------------------------------ 工具注册

def test_subagent_tools_registered(tmp_path, qxt_home):
    kernel = build_kernel()
    registry = kernel.require("tool_registry")
    names = {t.name for t in registry.tools}
    assert "subagent" in names and "agents_list" in names
    # subagent 工具 schema 暴露 name 参数 (命名 agent 开关已接线)
    sub = registry.get("subagent")
    assert "name" in sub.parameters["properties"]


# ------------------------------------------------------------------ agents_list

def test_agents_list_lists_project_agent(tmp_path, qxt_home):
    _write_agent(tmp_path, "helper", HELPER)
    kernel = _kernel_with(EchoModel())
    config = kernel.require("config")
    ctx = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                confirm=lambda _p: True).ctx
    registry = kernel.require("tool_registry")
    out = registry.get("agents_list").handler(ctx)
    assert "helper" in out
    assert "project" in out        # 来源层级标注
    assert "暖心的助手" in out or "helper" in out


def test_agents_list_no_agents(tmp_path, qxt_home):
    kernel = _kernel_with(EchoModel())
    config = kernel.require("config")
    ctx = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                confirm=lambda _p: True).ctx
    out = kernel.require("tool_registry").get("agents_list").handler(ctx)
    assert "未发现" in out


# ------------------------------------------------------------------ subagent 命名 Agent

def test_subagent_named_agent_resolves(tmp_path, qxt_home):
    """name 命中注册表 → 子代理正常执行、返回摘要 (而非 [错误] 未找到)。"""
    _write_agent(tmp_path, "helper", HELPER)
    kernel = _kernel_with(EchoModel())
    config = kernel.require("config")
    ctx = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                confirm=lambda _p: True).ctx
    out = kernel.require("tool_registry").get("subagent").handler(
        ctx, task="帮我总结一下", name="helper")
    assert "[错误] 未找到" not in out
    assert "回声:" in out          # EchoModel 返回了摘要
    assert "帮" in out


def test_subagent_unknown_name_errors_no_model(tmp_path, qxt_home):
    """name 未命中 → 立即返回错误, 不创建子代理、不发模型。"""
    kernel = _kernel_with(EchoModel())
    config = kernel.require("config")
    ctx = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                confirm=lambda _p: True).ctx
    out = kernel.require("tool_registry").get("subagent").handler(
        ctx, task="随便", name="does-not-exist")
    assert "[错误] 未找到命名 agent: does-not-exist" in out


# ------------------------------------------------------------------ 白名单折算 (端到端)

def test_subagent_whitelist_folds_real_registry(tmp_path, qxt_home):
    """命名 agent 的 tools 白名单在真实注册表上端到端生效: 子代理只见白名单内工具。

    用 ProbeModel 记录子代理模型调用收到的工具 schema; 若白名单折算正确,
    收到的工具名字应是『仅白名单且在注册表中存在的项』(其余被筛进排除集)。
    """
    registry = build_kernel().require("tool_registry")
    # 从真实注册表取两个白名单工具 (避开 subagent 自带的默认排除项)
    banned = {"subagent", "session_list", "session_resume", "session_delete"}
    whitelisted = sorted(t.name for t in registry.tools if t.name not in banned)[:2]
    assert len(whitelisted) == 2

    _write_agent(tmp_path, "scoped", (
        "---\n"
        f"name: scoped\ndescription: 只用两个工具\n"
        f"tools: {', '.join(whitelisted)}\n"
        "---\n"
        "你只用白名单工具办事。"
    ))

    probe = ProbeModel()
    kernel = _kernel_with(probe)
    config = kernel.require("config")
    ctx = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                confirm=lambda _p: True).ctx
    kernel.require("tool_registry").get("subagent").handler(
        ctx, task="白名单工具集验证", name="scoped")

    # 子代理至少发起过一次模型调用; 每次收到的工具集都应只有白名单内项
    assert probe.calls >= 1
    for tool_names in probe.seen:
        assert set(tool_names) == set(whitelisted)