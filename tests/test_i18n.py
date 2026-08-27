"""i18n 十语言系统测试: 键位对齐 / 取词插值 / 回退链 / 别名归一 / 首启选择器。"""

import importlib

import pytest

from qingxiaotuan.i18n import (
    DEFAULT_LANGUAGE, LANGUAGES, available_languages, choose_language_interactive,
    ensure_language, get_language, normalize_language, set_language, t,
)

LOCALE_CODES = list(LANGUAGES)


@pytest.fixture(autouse=True)
def _restore_default_language():
    """每个用例结束后恢复默认语言 (t() 是进程级全局状态)。"""
    yield
    set_language(DEFAULT_LANGUAGE)


def _locale(code: str):
    return importlib.import_module(
        "qingxiaotuan.i18n.locales." + code.replace("-", "_"))


# ------------------------------------------------------------------ 语言清单

def test_ten_languages_registered():
    assert len(LOCALE_CODES) == 10
    for info in available_languages().values():
        assert info["native"] and info["english"]


def test_all_locales_cover_same_keys_as_en():
    en_keys = set(_locale("en").TRANSLATIONS)
    assert len(en_keys) >= 80  # 键规模防回归: 意外删键会在这里暴露
    for code in LOCALE_CODES:
        keys = set(_locale(code).TRANSLATIONS)
        missing = en_keys - keys
        extra = keys - en_keys
        assert not missing, f"{code} 缺少键: {sorted(missing)[:5]}"
        assert not extra, f"{code} 多出键: {sorted(extra)[:5]}"


# ------------------------------------------------------------------ 取词与插值

def test_t_switch_and_interpolation():
    set_language("zh-CN")
    assert t("fs.context_pct", pct=62) == "上下文 62%"
    for code in ("ja", "de"):
        set_language(code)
        expected = _locale(code).TRANSLATIONS["fs.context_pct"].format(pct=62)
        assert t("fs.context_pct", pct=62) == expected
        assert "62" in t("fs.context_pct", pct=62)


def test_t_plain_lookup_matches_module():
    set_language("ja")
    assert t("cmd.exit") == _locale("ja").TRANSLATIONS["cmd.exit"]
    set_language("de")
    assert t("cmd.exit") == "Beenden"


def test_t_interpolation_edge_cases():
    set_language("zh-CN")
    # 缺 kwarg: 返回原模板, 不抛异常
    assert t("fs.context_pct") == "上下文 {pct}%"
    # 多余 kwarg: 正常忽略
    assert t("fs.context_pct", pct=1, junk="x") == "上下文 1%"
    # 完全未知的 key: 返回 key 本身
    assert t("no.such.key") == "no.such.key"


# ------------------------------------------------------------------ 回退链

def test_fallback_chain_current_to_zh_to_en_to_key():
    saved = {}
    for code in ("ja", "zh-CN", "en"):
        saved[code] = _locale(code).TRANSLATIONS.pop("cmd.exit")
    try:
        set_language("ja")
        # 三层全缺 -> key 本身
        assert t("cmd.exit") == "cmd.exit"
        _locale("en").TRANSLATIONS["cmd.exit"] = saved["en"]
        # 补回 en -> 命中 en
        assert t("cmd.exit") == saved["en"]
        _locale("zh-CN").TRANSLATIONS["cmd.exit"] = saved["zh-CN"]
        # 补回 zh-CN -> zh-CN 优先于 en
        assert t("cmd.exit") == saved["zh-CN"]
    finally:
        for code in ("ja", "zh-CN", "en"):
            _locale(code).TRANSLATIONS["cmd.exit"] = saved[code]


# ------------------------------------------------------------------ 归一与切换

@pytest.mark.parametrize("raw,expected", [
    ("zh", "zh-CN"), ("ZH-cn", "zh-CN"), ("chs", "zh-CN"), ("zh_CN", "zh-CN"),
    ("zh-hant", "zh-TW"), ("cht", "zh-TW"),
    ("jp", "ja"), ("JA_jp", "ja"),
    ("ko-KR", "ko"), ("en-US", "en"), ("pt-br", "pt-BR"), ("pt_BR", "pt-BR"),
    ("fr-FR", "fr"), ("de-de", "de"), ("ru-RU", "ru"),
    ("", ""), ("xx", ""), ("not-a-lang", ""),
])
def test_normalize_aliases(raw, expected):
    assert normalize_language(raw) == expected


def test_set_language_invalid_falls_back_to_default():
    assert set_language("xx-invalid") == DEFAULT_LANGUAGE
    assert get_language() == DEFAULT_LANGUAGE


def test_set_language_roundtrip():
    for code in LOCALE_CODES:
        assert set_language(code) == code
        assert get_language() == code


# ------------------------------------------------------------------ 首启选择器

class _StubConfig:
    """最小 config 桩: 只需 get/set_user 两个接口。"""

    def __init__(self, value=""):
        self.value = value
        self.saved = None

    def get(self, key, default=""):
        return self.value if key == "language" else default

    def set_user(self, key, val):
        self.saved = (key, val)


def test_ensure_language_applies_configured_value():
    cfg = _StubConfig("ja")
    assert ensure_language(cfg) == "ja"
    assert get_language() == "ja"
    assert cfg.saved is None  # 已配置 -> 不回写


def test_ensure_language_configured_alias_normalized():
    cfg = _StubConfig("jp")
    assert ensure_language(cfg) == "ja"


def test_ensure_language_noninteractive_never_blocks(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    called = []
    monkeypatch.setattr("qingxiaotuan.i18n.choose_language_interactive",
                        lambda: called.append(1) or DEFAULT_LANGUAGE)
    cfg = _StubConfig("")
    assert ensure_language(cfg) == DEFAULT_LANGUAGE
    assert not called  # 非交互环境绝不弹菜单


def test_choose_language_interactive_numeric(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "4")
    assert choose_language_interactive() == LOCALE_CODES[3]  # 第 4 项 = ja


def test_choose_language_interactive_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert choose_language_interactive() == DEFAULT_LANGUAGE


def test_choose_language_eof_returns_default(monkeypatch):
    def _eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    assert choose_language_interactive() == DEFAULT_LANGUAGE


# ------------------------------------------------------------------ 系统提示注入

def test_system_prompt_reply_language_rule(tmp_path):
    from qingxiaotuan.core.prompts import build_system_prompt

    base = dict(home=tmp_path / "home", workspace=str(tmp_path))
    (tmp_path / "home").mkdir()

    zh_prompt = build_system_prompt(reply_language="zh-CN", **base)
    ja_prompt = build_system_prompt(reply_language="ja", **base)

    # zh-CN 保持历史原文 (缓存友好, 不含英文语言名), 其他语言注入对应回复语言指令
    assert "Always reply in" not in zh_prompt
    assert "Always reply in Japanese" in ja_prompt
