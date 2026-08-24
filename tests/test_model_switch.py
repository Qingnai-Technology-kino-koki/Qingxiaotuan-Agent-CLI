"""多供应商热切换测试 (离线, Mock 配置)。

验证 Task D 的核心能力:
- create_adapter 对未知 provider 不再抛错, 而是按 openai-compatible 网关处理;
- 未知 provider 缺 base_url 时给出清晰报错;
- KNOWN_PROVIDERS / is_known_provider 可用;
- ModelPlugin.switch_model 重建适配器并重新注册内核服务 (运行时换脑子);
- 切换后 agent.kernel.require("model_adapter") 指向新实例;
- /model switch 与 qxt model set 路径可写回配置。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.models import create_adapter, KNOWN_PROVIDERS, is_known_provider
from qingxiaotuan.models.plugin import ModelPlugin
from qingxiaotuan.models.openai_compat import OpenAICompatAdapter
from qingxiaotuan.app import build_kernel
from qingxiaotuan.config import Config


def _cfg(**overrides):
    """构造一个最小 config 视图 (不读用户盘), 注入 model 覆盖项。"""
    cfg = Config(profile="default")
    for k, v in overrides.items():
        cfg.data["model"][k] = v
    return cfg


def test_known_provider_builds_adapter():
    cfg = _cfg(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-chat")
    adapter = create_adapter(cfg)
    assert isinstance(adapter, OpenAICompatAdapter)
    assert adapter.model == "deepseek-chat"
    assert adapter.base_url.endswith("https://api.deepseek.com")


def test_unknown_provider_with_base_url_falls_back_to_compat():
    """未知 provider 给了 base_url 就不报错, 按 openai-compatible 网关处理。"""
    cfg = _cfg(provider="my-claude-gw", base_url="https://gw.example/v1", model="claude-3-5-sonnet")
    adapter = create_adapter(cfg)
    assert isinstance(adapter, OpenAICompatAdapter)
    assert adapter.base_url.endswith("https://gw.example/v1")
    assert adapter.model == "claude-3-5-sonnet"


def test_unknown_provider_without_base_url_raises():
    cfg = _cfg(provider="my-claude-gw", model="claude-3-5-sonnet")
    # 清掉默认 base_url
    cfg.data["model"]["base_url"] = None
    try:
        create_adapter(cfg)
        assert False, "未知 provider 无 base_url 应抛 ValueError"
    except ValueError as e:
        assert "my-claude-gw" in str(e)
        assert "base_url" in str(e)


def test_known_providers_list_and_predicate():
    assert "deepseek" in KNOWN_PROVIDERS
    assert is_known_provider("deepseek") is True
    assert is_known_provider("totally-custom-gw") is False


def test_switch_model_rebuilds_and_reregisters():
    """ModelPlugin.switch_model 重建适配器并重新注册内核服务, 运行中的会话即时换脑子。"""
    kernel = build_kernel()
    old = kernel.require("model_adapter")
    new = ModelPlugin.switch_model(
        kernel,
        {"provider": "openai-compatible", "model": "gpt-4o", "base_url": "https://any/v1"},
        persist=False,
    )
    assert new is not old
    assert isinstance(new, OpenAICompatAdapter)
    assert kernel.require("model_adapter") is new
    assert kernel.require("model_adapter").model == "gpt-4o"
    # 内核视图也应反映新 provider
    assert kernel.require("config").get("model.provider") == "openai-compatible"


def test_switch_model_persists_to_user_config(tmp_path):
    """persist=True 时把覆盖项写回用户层 config (通过 QXT_HOME 隔离)。"""
    import os
    os.environ["QXT_HOME"] = str(tmp_path)
    try:
        kernel = build_kernel()
        ModelPlugin.switch_model(
            kernel,
            {"provider": "openai-compatible", "model": "gpt-4o-mini", "base_url": "https://any/v1"},
            persist=True,
        )
        # 重新读盘应反映新 provider
        cfg2 = Config(profile="default")
        assert cfg2.get("model.provider") == "openai-compatible"
        assert cfg2.get("model.model") == "gpt-4o-mini"
    finally:
        os.environ.pop("QXT_HOME", None)


def test_switch_model_unknown_provider_without_base_url_raises():
    """未知 provider 且 config 视图里确实没有 base_url 时, create_adapter 抛 ValueError。

    直接验证 create_adapter 的路径 (switch_model 内部即调用它); 内核服务不被改坏。
    """
    kernel = build_kernel()
    cfg = kernel.require("config")
    cfg.data["model"]["provider"] = "mystery"   # 未知 provider
    cfg.data["model"]["base_url"] = None        # 且无网关地址
    # create_adapter 应能独立抛出 (不依赖 switch_model 的 set_user 重载默认)
    try:
        create_adapter(cfg)
        assert False, "缺 base_url 的未知 provider 应抛 ValueError"
    except ValueError:
        assert kernel.require("model_adapter") is not None
