"""多模态 / 视觉能力测试 (M12)。

不依赖网络: 用 FakeModel 记录发送给模型的 messages, 断言图片块是否正确构造;
用临时 PNG 文件验证编码与门控逻辑; 用 Anthropic 适配器的静态转换法验证翻译。
"""

from __future__ import annotations

import base64
import os
import tempfile

import pytest

from qingxiaotuan.config import Config
from qingxiaotuan.models.base import ModelCapabilities, ModelResponse, ToolCall
from qingxiaotuan.models.anthropic import AnthropicAdapter
from qingxiaotuan.core.agent import Agent
from qingxiaotuan.vision import (
    ImageRef,
    encode_image_source,
    build_user_content,
    build_tool_content,
)
from qingxiaotuan.vision.blocks import encode_image_file
from qingxiaotuan.tools.base import ToolResult

# 1x1 透明 PNG (合法图片, 67 字节)
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLv"
    "AAAAAElFTkSuQmCC"
)


@pytest.fixture
def png_path():
    data = base64.b64decode(_PNG_B64)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "px.png")
    with open(p, "wb") as f:
        f.write(data)
    yield p
    import shutil

    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 编码


def test_encode_file_ok(png_path):
    ref = encode_image_file(png_path)
    assert ref.media_type == "image/png"
    assert ref.source == "file"
    assert ref.byte_size() == len(base64.b64decode(_PNG_B64))
    assert ref.data_uri().startswith("data:image/png;base64,")


def test_encode_rejects_non_image(tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError):
        encode_image_file(str(bad))


def test_encode_rejects_missing():
    with pytest.raises(FileNotFoundError):
        encode_image_file("/no/such/file.png")


def test_encode_url_passthrough():
    ref = encode_image_source("https://example.com/a.png")
    assert ref.source == "url"
    assert ref.is_remote()
    assert ref.data_uri() == "https://example.com/a.png"


def test_encode_data_uri_passthrough():
    uri = "data:image/png;base64," + _PNG_B64
    ref = encode_image_source(uri)
    assert ref.source == "data"
    assert ref.data_uri() == uri


# ---------------------------------------------------------------- builder 门控


def test_build_user_content_no_images_returns_str():
    assert build_user_content("hi", [], vision_capable=True) == "hi"
    assert build_user_content("hi", [], vision_capable=False) == "hi"


def test_build_user_content_vision_sends_blocks(png_path):
    ref = encode_image_file(png_path)
    out = build_user_content("看这张图", [ref], vision_capable=True)
    assert isinstance(out, list)
    assert out[0] == {"type": "text", "text": "看这张图"}
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_user_content_no_vision_degrades_to_note(png_path):
    ref = encode_image_file(png_path)
    out = build_user_content("看这张图", [ref], vision_capable=False)
    assert isinstance(out, str)
    assert "未送达模型" in out
    assert png_path in out
    assert "image_url" not in out


def test_build_tool_content_vision(png_path):
    ref = encode_image_file(png_path)
    out = build_tool_content("结果", [ref], vision_capable=True)
    assert isinstance(out, list)
    assert out[1]["type"] == "image_url"


def test_build_tool_content_no_vision_returns_text(png_path):
    ref = encode_image_file(png_path)
    assert build_tool_content("结果", [ref], vision_capable=False) == "结果"


# ---------------------------------------------------------------- Anthropic 翻译


def test_anthropic_translates_image_url():
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "看"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _PNG_B64}},
        ]}
    ]
    system, converted = AnthropicAdapter._convert_messages(messages)
    assert converted[0]["role"] == "user"
    blocks = converted[0]["content"]
    img = [b for b in blocks if b.get("type") == "image"][0]
    assert img["source"]["type"] == "base64"
    assert img["source"]["media_type"] == "image/png"
    assert img["source"]["data"] == _PNG_B64


def test_anthropic_tool_result_with_image():
    messages = [
        {"role": "tool", "tool_call_id": "t1", "name": "shot", "content": [
            {"type": "text", "text": "截图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _PNG_B64}},
        ]}
    ]
    system, converted = AnthropicAdapter._convert_messages(messages)
    tr = converted[0]["content"][0]
    assert tr["type"] == "tool_result"
    assert any(b.get("type") == "image" for b in tr["content"])


# ---------------------------------------------------------------- Agent 集成 (FakeModel)


class _FakeModel:
    name = "openai-compat"

    def __init__(self, vision):
        self.capabilities = ModelCapabilities(vision=vision)
        self.sent = []

    def chat(self, messages, tools=None, stream=False, on_token=None, on_reason=None):
        # 存副本: agent.messages 会在 chat 返回后被原地追加 assistant, 不能存引用
        self.sent.append([dict(m) for m in messages])
        return ModelResponse(content="ok")


class _FakeRegistry:
    def schemas(self):
        return []

    def dispatch(self, name, args, ctx):
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


def _make_agent(vision):
    model = _FakeModel(vision)
    kernel = _FakeKernel(model, _FakeRegistry())
    agent = Agent(
        kernel,
        Config(),
        workspace=os.getcwd(),
        context_manager=_FakeContext(),
    )
    return agent, model


def test_agent_attach_image_sends_blocks(png_path):
    agent, model = _make_agent(vision=True)
    agent.attach_image(png_path)
    agent.run("看这张图")
    last_user = model.sent[0][1]  # [0]=system, [1]=user
    assert last_user["role"] == "user"
    assert isinstance(last_user["content"], list)
    assert last_user["content"][1]["type"] == "image_url"
    # 发送后缓冲清空
    assert agent.pending_images == []


def test_agent_attach_image_no_vision_degrades(png_path):
    agent, model = _make_agent(vision=False)
    agent.attach_image(png_path)
    agent.run("看这张图")
    last_user = model.sent[0][1]
    assert isinstance(last_user["content"], str)
    assert "未送达模型" in last_user["content"]


def test_agent_tool_result_image(png_path):
    agent, model = _make_agent(vision=True)
    ref = encode_image_file(png_path)
    result = ToolResult(status="ok", content="图来了", images=[ref])
    content = agent._tool_content(result)
    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"


def test_agent_tool_result_image_no_vision_returns_result(png_path):
    agent, model = _make_agent(vision=False)
    ref = encode_image_file(png_path)
    result = ToolResult(status="ok", content="图来了", images=[ref])
    # 无视觉: 返回原 ToolResult (下游 str 化), 不泄漏图片字节
    assert agent._tool_content(result) is result


def test_attach_image_rejects_non_image(tmp_path):
    agent, _ = _make_agent(vision=True)
    bad = tmp_path / "x.txt"
    bad.write_text("nope")
    with pytest.raises(ValueError):
        agent.attach_image(str(bad))
