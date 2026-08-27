"""模型目录与 qxt model 选择流程测试 (离线)。

验证:
- 聚合平台 (OpenRouter/SiliconFlow/Novita/Together/Fireworks/Groq/NVIDIA NIM) 模型数量充足,
  OpenRouter 200+, 其余 50+;
- 每个模型带 |free/|paid 标注, OpenRouter 有免费模型;
- 直连平台 (DeepSeek/OpenAI/Anthropic/Gemini/通义/Kimi/智谱/豆包) 只列当前支持模型,
  不含已下架老模型;
- 默认模型 = 清单第一项 (剥离标注);
- _pick_model_interactive 按编号/自定义名选择, 返回剥离标注后的模型 ID;
- cmd_model list 子命令输出模型清单。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.models.provider_catalog import (
    ALL_PROVIDERS, get_provider, get_provider_models,
)
from qingxiaotuan.cli.commands import _pick_model_interactive


AGGREGATORS = ["openrouter", "siliconflow", "novita", "together", "fireworks", "groq", "nvidia-nim"]
# Groq 是直连平台 (自托管模型), 模型本来就少, 不要求 50+
AGGREGATORS_50 = ["siliconflow", "novita", "together", "fireworks", "nvidia-nim"]


def _strip(m: str) -> str:
    return m.split("|")[0] if "|" in m else m


def test_aggregators_have_50_plus_models():
    for name in AGGREGATORS_50:
        models = get_provider_models(name)
        assert len(models) >= 50, f"{name} 只有 {len(models)} 个模型, 应 >= 50"
        assert len(set(models)) == len(models), f"{name} 模型清单有重复"


def test_openrouter_has_200_plus_models():
    models = get_provider_models("openrouter")
    assert len(models) >= 200, f"openrouter 只有 {len(models)} 个模型, 应 >= 200"


def test_model_payment_labels():
    """每个模型都带 |free/|paid 标注, 且 OpenRouter 有免费模型。"""
    for name in AGGREGATORS:
        for m in get_provider_models(name):
            assert m.endswith("|free") or m.endswith("|paid"), (
                f"{name} 模型缺少付费标注: {m}"
            )
    or_models = get_provider_models("openrouter")
    free_count = sum(1 for m in or_models if m.endswith("|free"))
    assert free_count >= 10, f"openrouter 免费模型数量不足, 实际 {free_count}"


def test_direct_platforms_only_current_models():
    """直连平台只列当前支持模型, 不含已下架老模型。"""
    # DeepSeek: 应包含 v4 系列当前模型, 不含已停用的 deepseek-v2 等
    ds = [_strip(m) for m in get_provider_models("deepseek")]
    assert "deepseek-v4-flash" in ds
    assert "deepseek-v4-pro" in ds
    assert "deepseek-v4-flash-0731" in ds
    assert "deepseek-v4-pro-0813" in ds
    assert "deepseek-chat" in ds
    assert not any("v2" in m or "v1" in m for m in ds), f"DeepSeek 混入老模型: {ds}"
    # OpenAI: 不含 gpt-3.5 / gpt-4-turbo 等已弃用模型
    oa = [_strip(m) for m in get_provider_models("openai")]
    assert "gpt-4o" in oa
    assert not any("gpt-3.5" in m or "gpt-4-turbo" in m for m in oa), f"OpenAI 混入弃用模型: {oa}"
    # Anthropic: 不含 claude-2 / claude-3 旧版
    an = [_strip(m) for m in get_provider_models("anthropic")]
    assert not any(m.startswith("claude-2") or m.startswith("claude-3-") for m in an), f"Anthropic 混入旧模型: {an}"


def test_default_model_is_first_in_list():
    for p in ALL_PROVIDERS:
        if p.recommended_models:
            assert p.model == _strip(p.recommended_models[0]), (
                f"{p.name} 默认模型 {p.model} 与清单第一项 {p.recommended_models[0]} 不一致"
            )


def test_get_provider_models_unknown_returns_empty():
    assert get_provider_models("no-such-provider") == []


def test_every_provider_has_default_model():
    for p in ALL_PROVIDERS:
        assert p.model, f"{p.name} 缺少默认模型"


def test_pick_interactive_by_number(monkeypatch):
    p = get_provider("deepseek")
    inputs = iter(["3"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    prov, model = _pick_model_interactive(p)
    assert prov == p.name
    assert model == _strip(p.recommended_models[2])


def test_pick_interactive_default_first(monkeypatch):
    p = get_provider("deepseek")
    inputs = iter([""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    prov, model = _pick_model_interactive(p)
    assert prov == p.name
    assert model == _strip(p.recommended_models[0])


def test_pick_interactive_custom_name(monkeypatch):
    p = get_provider("deepseek")
    inputs = iter(["my-custom-model"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    prov, model = _pick_model_interactive(p)
    assert prov == p.name
    assert model == "my-custom-model"


def test_pick_interactive_out_of_range_falls_back(monkeypatch):
    p = get_provider("deepseek")
    inputs = iter(["999"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    prov, model = _pick_model_interactive(p)
    assert prov == p.name
    assert model == _strip(p.recommended_models[0])


def test_pick_interactive_strips_label(monkeypatch):
    """选择带标注的模型时, 返回剥离标注后的纯模型 ID。"""
    p = get_provider("openrouter")
    inputs = iter(["1"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    prov, model = _pick_model_interactive(p)
    assert "|" not in model, f"返回的模型名不应带标注: {model}"
    assert model == _strip(p.recommended_models[0])


def test_pick_interactive_search_returns_other_provider(monkeypatch):
    """输入 search <关键词> 可跨供应商搜索并返回 (provider, model)。"""
    p = get_provider("deepseek")
    inputs = iter(["search deepseek-v4", "1"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    prov, model = _pick_model_interactive(p)
    assert "deepseek-v4" in model


def test_search_models_by_model_id():
    from qingxiaotuan.models.provider_catalog import search_models
    results = search_models("deepseek-v4")
    assert results
    for prov, m, free in results:
        assert "deepseek-v4" in m.lower()
        assert isinstance(free, bool)


def test_search_models_by_provider_name():
    from qingxiaotuan.models.provider_catalog import search_models
    results = search_models("openrouter")
    assert results
    assert all(prov == "openrouter" for prov, _, _ in results)


def test_search_models_no_match_returns_empty():
    from qingxiaotuan.models.provider_catalog import search_models
    assert search_models("zzz-no-such-model-xyz") == []
