"""M3: 统一模型能力矩阵测试
覆盖 OpenAI/Anthropic 协议转换、能力声明、错误处理。
"""
import json
import os
import sys
from pathlib import Path

import pytest

# 确保可以导入 qingxiaotuan
sys.path.insert(0, str(Path(__file__).parent.parent))

from qingxiaotuan.models.base import ModelCapabilities
from qingxiaotuan.models.openai_compat import OpenAICompatAdapter
from qingxiaotuan.models.anthropic import AnthropicAdapter


def test_model_capabilities_defaults():
    """测试 ModelCapabilities 默认声明"""
    caps = ModelCapabilities()
    assert caps.tool_calling is False
    assert caps.streaming is False
    assert caps.vision is False
    assert caps.json_mode is False


def test_model_capabilities_custom():
    """测试自定义能力声明"""
    caps = ModelCapabilities(
        tool_calling=True,
        streaming=True,
        vision=True,
        json_mode=True,
    )
    assert caps.vision is True
    assert caps.json_mode is True


def test_openai_compat_capabilities():
    """测试 OpenAI-compatible 能力声明"""
    caps = OpenAICompatAdapter.capabilities
    assert caps.tool_calling is True
    assert caps.streaming is True


def test_anthropic_capabilities():
    """测试 Anthropic 能力声明"""
    caps = AnthropicAdapter.capabilities
    assert caps.tool_calling is True
    assert caps.streaming is True


def test_provider_factory_switch():
    """测试运行时热切换不破坏 Agent 契约"""
    from qingxiaotuan.models import create_adapter
    from qingxiaotuan.config.loader import Config

    config = Config()
    config.data["model"]["provider"] = "deepseek"
    config.data["model"]["model"] = "deepseek-chat"
    config.data["model"]["base_url"] = "https://api.deepseek.com"

    adapter = create_adapter(config)
    assert adapter is not None
    assert hasattr(adapter, "chat")
    assert hasattr(adapter, "capabilities")


def test_gemini_routes_to_openai_compat_adapter():
    """M3 验收: Gemini 通过 OpenAI 兼容端点接入, 能力声明含工具/流式/视觉。"""
    from qingxiaotuan.models import create_adapter
    from qingxiaotuan.models.openai_compat import OpenAICompatAdapter
    from qingxiaotuan.config.loader import Config

    config = Config()
    config.data["model"]["provider"] = "gemini"
    config.data["model"]["model"] = "gemini-2.5-flash"
    config.data["model"]["base_url"] = "https://generativelanguage.googleapis.com/v1beta/openai"

    adapter = create_adapter(config)
    assert isinstance(adapter, OpenAICompatAdapter)
    caps = adapter.capabilities
    assert caps.tool_calling is True
    assert caps.streaming is True
    assert caps.vision is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
