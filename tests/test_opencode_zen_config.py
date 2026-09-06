"""OpenCode Zen 接入相关测试: 配置预设、密钥解析、超时分离、重试分类、日志脱敏。"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

openai = pytest.importorskip("openai", reason="需要 openai 包")

from qingxiaotuan.config import Config, PRESET_PROFILES, load_dotenv
from qingxiaotuan.models.openai_compat import OpenAICompatAdapter
from qingxiaotuan.logging_conf import redact, setup_logging
from qingxiaotuan.core.agent import Agent
from qingxiaotuan.core.retry import RateLimiter, RetryPolicy
from qingxiaotuan.models.provider_catalog import get_provider, get_provider_models


# ---------------------------------------------------------------- 预设 profile

def test_preset_opencode_zen_exists():
    assert "opencode-zen" in PRESET_PROFILES
    prof = PRESET_PROFILES["opencode-zen"]["model"]
    assert prof["base_url"] == "https://opencode.ai/zen/v1"
    assert prof["model"] == "deepseek-v4-free"
    assert prof["api_key_env"] == "OPENCODE_ZEN_API_KEY"


def test_preset_profile_applied_on_load(qxt_home):
    cfg = Config(profile="opencode-zen")
    assert cfg.get("model.provider") == "opencode-zen"
    assert cfg.get("model.base_url") == "https://opencode.ai/zen/v1"
    assert cfg.get("model.model") == "deepseek-v4-free"


def test_user_config_overrides_preset(qxt_home):
    # 用户层写了不同 model, 应覆盖预设
    qxt_home.mkdir(parents=True, exist_ok=True)
    (qxt_home / "config.yaml").write_text("model:\n  model: other-model\n", encoding="utf-8")
    cfg = Config(profile="opencode-zen")
    assert cfg.get("model.model") == "other-model"
    # 预设的 base_url 仍保留
    assert cfg.get("model.base_url") == "https://opencode.ai/zen/v1"


# ---------------------------------------------------------------- 模型目录

def test_opencode_zen_catalog_registered_and_default_first():
    preset = get_provider("opencode-zen")
    assert preset is not None
    # 默认模型 = 实际可用模型 (清单第一项), 且被目录覆盖为 deepseek-v4-free
    assert preset.model == "deepseek-v4-free"
    models = get_provider_models("opencode-zen")
    assert models and models[0].split("|")[0] == "deepseek-v4-free"
    # 完整登记官方广告目录的关键模型 (GPT/Claude/Gemini/DeepSeek/Grok/Kimi…)
    ids = {m.split("|")[0] for m in models}
    for expect in ("gpt-6-astra", "gpt-5.6-terra", "claude-opus-5",
                   "claude-sonnet-4-6", "gemini-3.7-flash", "grok-4.6",
                   "deepseek-v4-pro", "kimi-k3", "glm-5.3"):
        assert expect in ids, f"目录缺模型: {expect}"
    # 免费层模型带 |free 标注
    free_ids = {m.split("|")[0] for m in models if "|free" in m}
    for f in ("deepseek-v4-free", "nemotron-3-ultra-free",
              "muse-spark-1.3-contributor-free"):
        assert f in free_ids, f"免费模型应标注 free: {f}"
    # 只注册了一次 (去重)
    assert len(models) == len(ids)


# ---------------------------------------------------------------- 密钥解析

def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-test-123")
    cfg = Config()
    cfg.set_user("model.api_key_env", "OPENCODE_ZEN_API_KEY")
    assert cfg.api_key() == "sk-test-123"


def test_api_key_fallback_to_qxt(monkeypatch):
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)
    monkeypatch.setenv("QXT_API_KEY", "sk-qxt-fallback")
    cfg = Config()
    assert cfg.api_key() == "sk-qxt-fallback"


def test_api_key_ref_resolution(qxt_home, monkeypatch):
    monkeypatch.setenv("MY_CUSTOM_KEY", "sk-custom")
    cfg = Config()
    cfg.set_user("model.api_key_env", "DOES_NOT_EXIST")
    cfg.set_user("model.api_key_ref", "MY_CUSTOM_KEY")
    assert cfg.api_key() == "sk-custom"


def test_dotenv_loading(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENCODE_ZEN_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    load_dotenv(tmp_path)
    assert os.environ.get("OPENCODE_ZEN_API_KEY") == "sk-from-dotenv"


# ---------------------------------------------------------------- 超时分离

def test_adapter_timeout_separation():
    import openai
    with mock.patch.object(openai, "OpenAI") as MockOpenAI:
        adapter = OpenAICompatAdapter(
            base_url="https://opencode.ai/zen/v1",
            model="deepseek-v4-flash-free",
            api_key="sk-x",
            connect_timeout=5.0,
            read_timeout=99.0,
        )
        _ = adapter.client
        # 验证 OpenAI 收到的是 httpx.Timeout 对象, 且连接/读分离
        _, kwargs = MockOpenAI.call_args
        timeout = kwargs["timeout"]
        assert timeout.connect == 5.0
        assert timeout.read == 99.0
        assert kwargs["max_retries"] == 0  # SDK 重试关闭, 由 agent 层控制


# ---------------------------------------------------------------- 错误分类

def test_classify_rate_limit():
    exc = RuntimeError("Rate limit exceeded")
    kind, status = OpenAICompatAdapter.classify_error(exc)
    assert kind == "rate_limit"
    assert status == 429


def test_classify_auth():
    class AuthErr(Exception):
        status_code = 401
    kind, status = OpenAICompatAdapter.classify_error(AuthErr())
    assert kind == "auth"
    assert status == 401


def test_classify_server():
    class SrvErr(Exception):
        status_code = 503
    kind, status = OpenAICompatAdapter.classify_error(SrvErr())
    assert kind == "server"
    assert status == 503


def test_classify_timeout():
    import httpx
    exc = httpx.ConnectTimeout("timed out")
    kind, status = OpenAICompatAdapter.classify_error(exc)
    assert kind == "timeout"


# ---------------------------------------------------------------- 重试逻辑 (agent 层)

def test_retry_backoff_with_jitter():
    """验证指数退避+抖动, 且 429 不立即放弃 (重试策略已拆到 core/retry.py)。"""
    sleeps = []
    with mock.patch("qingxiaotuan.core.retry.time.sleep", side_effect=lambda s: sleeps.append(s)):
        adapter = mock.MagicMock()
        adapter.chat.side_effect = [
            RuntimeError("Rate limit"),  # 第1次失败
            RuntimeError("Rate limit"),  # 第2次失败
            mock.MagicMock(content="ok", reasoning="", tool_calls=[], finish_reason="stop", usage={}),
        ]
        # 构造最小 Agent
        kernel = mock.MagicMock()
        kernel.require.return_value = adapter
        kernel.get.return_value = None
        cfg = Config()
        agent = Agent.__new__(Agent)
        agent.model = adapter
        agent.retry_policy = RetryPolicy(max_retries=3, backoff=2.0, jitter=0.0,
                                         retry_on={429, 500, 503})
        agent._rate_limiter = RateLimiter(0, 0)  # 禁用限流, 聚焦重试语义
        agent.kernel = kernel
        resp = agent._chat_with_retry([{"role": "user", "content": "x"}], tools=None,
                                       stream=False, on_token=None, on_reason=None)
        assert resp.content == "ok"
        # 两次退避: 2*2^0=2, 2*2^1=4
        assert sleeps == [2.0, 4.0]


def test_auth_error_immediately_raises():
    with mock.patch("qingxiaotuan.core.retry.time.sleep"):
        class AuthExc(Exception):
            status_code = 401
        adapter = mock.MagicMock()
        adapter.chat.side_effect = AuthExc("unauthorized")
        kernel = mock.MagicMock()
        cfg = Config()
        agent = Agent.__new__(Agent)
        agent.model = adapter
        agent.retry_policy = RetryPolicy(max_retries=3, backoff=2.0, jitter=0.0,
                                         retry_on={429, 500})
        agent._rate_limiter = RateLimiter(0, 0)  # 禁用限流, 聚焦重试语义
        agent.kernel = kernel
        try:
            agent._chat_with_retry([], tools=None, stream=False, on_token=None, on_reason=None)
            assert False, "should have raised"
        except RuntimeError as e:
            assert "鉴权失败" in str(e)


# ---------------------------------------------------------------- 日志脱敏

def test_redact_key():
    key = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    masked = redact(key)
    # 保留前缀 sk- + 8 位 (sk-ABCDEFG), 其余打码
    assert masked.startswith("sk-ABCDEFG")
    assert "***" in masked
    # 尾部片段不应出现在脱敏结果中
    assert key[-4:] not in masked
    assert len(key) > len(masked) + 20


def test_setup_logging_creates_file(tmp_path):
    logger = setup_logging(tmp_path, level=20, audit=False)
    logger.info("hello %s", "world")
    log_files = list((tmp_path / "logs").glob("qxt-*.log"))
    assert log_files
    content = log_files[0].read_text(encoding="utf-8")
    assert "hello world" in content
