"""自动模型路由测试 (M13)。

不依赖网络:
- 路由决策逻辑用纯函数断言 (难度评估 / 能力门控 / 升级降级 / 失败保险)。
- Agent 集成用 FakeModel + 注入式 _model_switcher, 验证「是否真的换脑子」与挂图视觉门控。
"""

from __future__ import annotations

import os

import pytest

from qingxiaotuan.config import Config
from qingxiaotuan.models.base import ModelCapabilities, ModelResponse
from qingxiaotuan.models.router import ModelRouter
from qingxiaotuan.core.agent import Agent

# 测试中允许路由切换到的供应商全集 (绕过环境变量, 保证确定性)
_BROAD = [
    "deepseek", "groq", "zhipu", "qwen", "doubao", "openai",
    "gemini", "anthropic", "moonshot", "mistral", "xai",
]

# 哪些模型支持视觉 (用于 FakeModel 能力映射)
_VISION_MODELS = {
    "gpt-4o-mini", "gpt-4o", "gemini-2.5-flash",
    "claude-sonnet-4-20250514", "claude-3-opus-20240229", "grok-3",
}


@pytest.fixture
def router():
    return ModelRouter(default_provider="deepseek", default_model="deepseek-chat")


@pytest.fixture(autouse=True)
def isolated_qxt_home(monkeypatch, tmp_path):
    """隔离 QXT_HOME, 确保任何 Config()/set_user 只写到临时目录, 绝不触碰用户真实配置。"""
    monkeypatch.setenv("QXT_HOME", str(tmp_path))
    yield


@pytest.fixture
def patch_available(monkeypatch):
    monkeypatch.setattr(
        ModelRouter, "available_provider_names",
        staticmethod(lambda: list(_BROAD)),
    )


# ---------------------------------------------------------------- 难度评估


def test_estimate_difficulty_easy(router):
    d = router.estimate_difficulty("查询一下今天的天气并列出结果")
    assert 1 <= d <= 4


def test_estimate_difficulty_hard(router):
    d = router.estimate_difficulty("设计分布式系统的架构, 做安全审计与性能优化, 涉及加密与认证")
    assert d >= 8


def test_decide_difficulty_override(router, patch_available):
    d = router.decide(
        "随便说点什么", current_provider="deepseek", current_model="deepseek-chat",
        difficulty_override=7,
    )
    assert d["difficulty"] == 7


# ---------------------------------------------------------------- 密钥失败保险


def test_available_provider_names_env_gated(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert "groq" not in ModelRouter.available_provider_names()
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert "groq" in ModelRouter.available_provider_names()


# ---------------------------------------------------------------- decide 决策


def test_decide_downgrades_easy_task(router, patch_available):
    d = router.decide("查询并列出文件", current_provider="deepseek", current_model="deepseek-chat")
    assert d["switch"] is True
    # 降级到更便宜的档位 (deepseek-chat 是 tier2, 应切到 tier1 免费/低价模型)
    assert d["reason"]  # 有理由文本


def test_decide_escalates_hard_task(router, patch_available):
    d = router.decide(
        "设计分布式系统的架构, 做安全审计与性能优化",
        current_provider="deepseek", current_model="deepseek-chat",
    )
    assert d["switch"] is True
    # 升级后必为 tier3 强模型
    preset = router._preset_for(d["provider"], d["model"])
    assert preset is not None and preset.tier == 3


def test_decide_routes_to_vision_when_images_attached(router, patch_available):
    d = router.decide(
        "看这张图并描述内容",
        current_provider="deepseek", current_model="deepseek-chat",
        has_images=True,
    )
    assert d["switch"] is True
    assert "vision" in d["capabilities"]


def test_decide_no_switch_when_already_optimal(router, patch_available, monkeypatch):
    # 仅 deepseek 可用, 当前就是 deepseek-chat → 无更优可切
    # (decide 消费的是 available_providers 参数, 而非 available_provider_names 静态方法;
    #  显式传入 ["deepseek"] 才能真正把候选集限制到 deepseek 一族)
    d = router.decide(
        "实现一个小工具函数", current_provider="deepseek", current_model="deepseek-chat",
        available_providers=["deepseek"],
    )
    assert d["switch"] is False
    assert (d["provider"], d["model"]) == ("deepseek", "deepseek-chat")


def test_decide_respects_custom_model(router, patch_available):
    # 自定义/未知模型 (不在预设) → 不自动路由, 尊重用户指定
    d = router.decide(
        "实现一个小工具函数", current_provider="my-gateway", current_model="my-model",
    )
    assert d["switch"] is False
    assert "自定义" in d["reason"]


def test_decide_no_switch_when_only_cheaper_unavailable(router, patch_available):
    # 当前已是免费 tier1 模型, 简单任务 → 保持 (没有更便宜的了)
    d = router.decide(
        "查询天气", current_provider="zhipu", current_model="glm-4-flash",
    )
    assert d["switch"] is False


def test_decide_no_switch_when_no_provider_has_key(router):
    """失败保险: 显式传入空可用供应商 (没有任何供应商配置密钥) → 绝不切换。

    回归防护: 空列表曾被当作「不过滤」, 导致路由切到无凭证的预设模型,
    把请求发到未配置 API Key 的端点 (e2e mock / loop / cache 测试全挂)。
    """
    d = router.decide(
        "读一下文件", current_provider="deepseek", current_model="deepseek-chat",
        available_providers=[],
    )
    assert d["switch"] is False
    assert (d["provider"], d["model"]) == ("deepseek", "deepseek-chat")
    assert "失败保险" in d["reason"] or "密钥" in d["reason"]


def test_decide_never_switches_outside_available_providers(router):
    """失败保险 (二): select_model 候选为空时会回退默认模型, 该默认可能不在
    可用列表内 —— decide 必须拒绝切换到未配置密钥的供应商。

    回归防护: 仅配置 ARK_API_KEY 时, 高难度任务曾把 doubao 切到无密钥的
    deepseek 默认档, 导致请求打到无凭证端点后重试三次炸掉。
    """
    # 当前 doubao (max_difficulty=5), 难度 8 → 可用列表内无候选 → 回退 deepseek
    d = router.decide(
        "设计分布式系统的架构, 做安全审计与性能优化",
        current_provider="doubao", current_model="doubao-1.5-pro-256k",
        difficulty_override=8, available_providers=["doubao"],
    )
    assert d["switch"] is False
    assert (d["provider"], d["model"]) == ("doubao", "doubao-1.5-pro-256k")
    assert "失败保险" in d["reason"]


# ---------------------------------------------------------------- Agent 集成 (FakeModel)


class _FakeModel:
    name = "openai-compat"

    def __init__(self, model_name: str, vision: bool):
        self.model = model_name
        self.capabilities = ModelCapabilities(vision=vision)
        self.sent = []

    def chat(self, messages, tools=None, stream=False, on_token=None, on_reason=None):
        # 存副本: agent.messages 在 chat 返回后被原地追加 assistant, 不能存引用
        self.sent.append([dict(m) for m in messages])
        return ModelResponse(content="ok")


class _FakeRegistry:
    def schemas(self):
        return []

    def dispatch(self, name, args, ctx):
        from qingxiaotuan.tools.base import ToolResult
        return ToolResult(status="ok", content="x")


class _FakeContext:
    def needs_compact(self, msgs):
        return False

    def compact_if_needed(self, msgs):
        return msgs, 0

    def compact_force(self, msgs):
        return msgs, 0


class _FakeKernel:
    def __init__(self, model, registry):
        self._model = model
        self._registry = registry

    def require(self, name):
        if name == "model_adapter":
            return self._model
        if name == "tool_registry":
            return self._registry
        raise KeyError(name)

    def get(self, name, default=None):
        return default

    def emit(self, *a, **k):
        pass

    def provide(self, name, val, owner=None):
        if name == "model_adapter":
            self._model = val


def _make_agent(model_name="deepseek-chat", vision=False):
    model = _FakeModel(model_name, vision)
    kernel = _FakeKernel(model, _FakeRegistry())
    agent = Agent(kernel, Config(), workspace=os.getcwd(), context_manager=_FakeContext())
    return agent, model, kernel


def _install_switcher(agent, kernel):
    """注入式切换器: 把 chosen 模型换成对应能力的 FakeModel (不触网)。"""
    def switcher(k, overrides):
        name = overrides.get("model")
        vision = name in _VISION_MODELS
        kernel.provide("model_adapter", _FakeModel(name, vision))
    agent._model_switcher = switcher


def test_agent_routes_hard_task_to_strong_model(patch_available):
    agent, model, kernel = _make_agent()
    _install_switcher(agent, kernel)
    agent.run("设计分布式系统架构并做安全审计与性能优化")
    assert agent._last_route is not None
    assert agent._last_route["switch"] is True
    # chosen 是 tier3 强模型
    assert agent.model.model in {
        "deepseek-reasoner", "gpt-4o", "grok-3", "claude-3-opus-20240229",
    }


def test_agent_routes_image_to_vision_model(patch_available, tmp_path):
    # 1x1 PNG
    import base64
    png = tmp_path / "px.png"
    png.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLv"
        "AAAAAElFTkSuQmCC"
    ))
    agent, model, kernel = _make_agent(model_name="deepseek-chat", vision=False)
    _install_switcher(agent, kernel)
    agent.attach_image(str(png))
    agent.run("看这张图描述一下")
    assert agent._last_route["switch"] is True
    assert agent.model.capabilities.vision is True
    # 切换后 user 消息应含 image_url 块 (chat 打在切换后的新模型上)
    user_msg = agent.model.sent[0][1]
    assert isinstance(user_msg["content"], list)
    assert any(b.get("type") == "image_url" for b in user_msg["content"])


def test_agent_no_switch_when_already_optimal(patch_available, monkeypatch):
    agent, model, kernel = _make_agent()
    _install_switcher(agent, kernel)
    # 仅 deepseek 可用, 当前即 deepseek-chat → 不切换
    monkeypatch.setattr(
        ModelRouter, "available_provider_names", staticmethod(lambda: ["deepseek"])
    )
    agent.run("实现一个小工具函数")
    assert agent._last_route["switch"] is False
    # 模型实例未被替换 (仍是初始 deepseek-chat FakeModel)
    assert agent.model is model
    assert agent.model.model == "deepseek-chat"


def test_agent_respects_custom_model_no_switch(patch_available):
    cfg = Config()
    cfg.set_user("model.provider", "my-gateway")
    cfg.set_user("model.model", "my-model")
    agent, model, kernel = _make_agent()
    agent.config = cfg
    _install_switcher(agent, kernel)
    agent.run("实现一个小工具函数")
    assert agent._last_route["switch"] is False
    assert agent.model is model  # 未被替换
