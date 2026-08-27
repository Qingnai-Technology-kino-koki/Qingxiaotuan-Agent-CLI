"""青小团 CLI 多语言支持 (i18n)。

十种界面语言: 简体中文 / 繁體中文 / English / 日本語 / 한국어 /
Español / Português do Brasil / Français / Deutsch / Русский。

用法::

    from qingxiaotuan.i18n import t, set_language, ensure_language
    set_language("ja")
    t("banner.welcome")            # -> "青小団 CLI へようこそ！"
    t("fs.context_pct", pct=62)    # -> "コンテキスト 62%"

设计要点:

- **懒加载**: 语言模块仅在首次取词时 import, 未用到的语言零启动开销。
- **回退链**: 当前语言 -> zh-CN -> en -> key 本身, 缺译永不抛异常。
- **默认 zh-CN**: 项目面向中文用户, 未配置时 UI 全中文, 与历史行为一致;
  因此既有测试的中文断言在默认状态下不受影响。
- **首启选择器**: ``ensure_language(config)`` 仅在交互式 TTY 且未配置语言时
  弹出编号选择菜单, 测试与 CI 等非交互环境绝不阻塞。
"""

from __future__ import annotations

import sys
from importlib import import_module
from typing import Any, Dict, Optional

__all__ = [
    "DEFAULT_LANGUAGE", "LANGUAGES", "available_languages",
    "normalize_language", "get_language", "set_language", "t",
    "ensure_language", "choose_language_interactive",
]

# ------------------------------------------------------------------ 语言清单

DEFAULT_LANGUAGE = "zh-CN"

# native: 该语言的本地自称 (菜单里用它展示); english: 英文名 (注入系统提示用)
LANGUAGES: Dict[str, Dict[str, str]] = {
    "zh-CN": {"native": "简体中文",             "english": "Simplified Chinese"},
    "zh-TW": {"native": "繁體中文",             "english": "Traditional Chinese"},
    "en":    {"native": "English",              "english": "English"},
    "ja":    {"native": "日本語",               "english": "Japanese"},
    "ko":    {"native": "한국어",                "english": "Korean"},
    "es":    {"native": "Español",              "english": "Spanish"},
    "pt-BR": {"native": "Português (Brasil)",   "english": "Portuguese (Brazil)"},
    "fr":    {"native": "Français",             "english": "French"},
    "de":    {"native": "Deutsch",              "english": "German"},
    "ru":    {"native": "Русский",              "english": "Russian"},
}

# 常见别名归一 (用户手填 config 或环境变量时容错)
_ALIASES = {
    "zh": "zh-CN", "zh-hans": "zh-CN", "zh-cn": "zh-CN", "chs": "zh-CN",
    "zh-hant": "zh-TW", "zh-tw": "zh-TW", "zh-hk": "zh-TW", "cht": "zh-TW",
    "en-us": "en", "en-gb": "en",
    "ja-jp": "ja", "jp": "ja",
    "ko-kr": "ko",
    "es-es": "es", "es-mx": "es", "es-419": "es",
    "pt-br": "pt-BR", "pt-pt": "pt-BR",
    "fr-fr": "fr",
    "de-de": "de",
    "ru-ru": "ru",
}

# ------------------------------------------------------------------ 运行时状态

_current: str = DEFAULT_LANGUAGE
_modules: Dict[str, Optional[Any]] = {}


def normalize_language(value: str) -> str:
    """把任意大小写/别名形式归一到 LANGUAGES 里的标准代码, 无法识别返回空串。"""
    if not value:
        return ""
    v = str(value).strip().replace("_", "-")
    if v in LANGUAGES:
        return v
    low = v.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    # BCP-47 主子标签兜底: "ZH-cn" / "JA_jp" 之类
    primary = low.split("-", 1)[0]
    if primary in _ALIASES:
        return _ALIASES[primary]
    return ""


def get_language() -> str:
    """当前生效的语言代码。"""
    return _current


def set_language(lang: str) -> str:
    """切换当前语言并返回归一后的代码 (无法识别时回落默认值)。"""
    global _current
    _current = normalize_language(lang) or DEFAULT_LANGUAGE
    return _current


def available_languages() -> Dict[str, Dict[str, str]]:
    """支持的语言清单副本 (供 setup 向导等外部调用方遍历)。"""
    return {code: dict(info) for code, info in LANGUAGES.items()}


# ------------------------------------------------------------------ 取词

def _locale_module(code: str):
    """懒加载语言模块, 失败缓存 None (下次不再尝试)。"""
    if code not in _modules:
        try:
            _modules[code] = import_module(
                f".locales.{code.replace('-', '_')}", __name__)
        except ImportError:
            _modules[code] = None
    return _modules[code]


def t(key: str, **kwargs) -> str:
    """按当前语言取词; kwargs 用于模板插值 (如 t("fs.context_pct", pct=62))。

    回退链: 当前语言 -> zh-CN -> en -> key 本身; 插值失败时返回原模板。
    """
    for code in (_current, DEFAULT_LANGUAGE, "en"):
        mod = _locale_module(code)
        if mod is not None:
            text: Optional[str] = getattr(mod, "TRANSLATIONS", {}).get(key)
            if text is not None:
                if not kwargs:
                    return text
                try:
                    return text.format(**kwargs)
                except (KeyError, IndexError, ValueError):
                    return text
    return key


# ------------------------------------------------------------------ 首启选择器

def choose_language_interactive() -> str:
    """交互式语言选择菜单 (input 编号选择, 不依赖 prompt_toolkit)。

    标题固定多语言并排 —— 此时还没有选定语言, 不能用 t() 取词。
    """
    codes = list(LANGUAGES)
    default_idx = codes.index(DEFAULT_LANGUAGE) + 1
    print()
    print("  请选择界面语言 / Select interface language / 言語を選択 / 언어 선택:")
    for i, code in enumerate(codes, 1):
        info = LANGUAGES[code]
        print(f"  {i:>2}. {info['native']:<22} ({info['english']})")
    try:
        raw = input(f"  > [{default_idx}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return DEFAULT_LANGUAGE
    print()
    if not raw:
        return DEFAULT_LANGUAGE
    if raw.isdigit():
        idx = int(raw) - 1
        return codes[idx] if 0 <= idx < len(codes) else DEFAULT_LANGUAGE
    return normalize_language(raw) or DEFAULT_LANGUAGE


def ensure_language(config=None) -> str:
    """确保界面语言就绪, 返回生效的语言代码。

    - 已配置 (config.get("language")) -> 直接应用, 不弹菜单;
    - 未配置且处于交互式 TTY      -> 弹一次选择菜单并写回 config;
    - 未配置且非交互 (测试/CI/管道) -> 应用默认 zh-CN, 绝不阻塞。
    """
    configured = ""
    if config is not None:
        try:
            configured = normalize_language(config.get("language", "") or "")
        except Exception:  # noqa: BLE001
            configured = ""
    if configured:
        return set_language(configured)
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        interactive = False
    if not interactive:
        return set_language(DEFAULT_LANGUAGE)
    chosen = choose_language_interactive()
    set_language(chosen)
    if config is not None:
        try:
            config.set_user("language", chosen)  # 写盘, 下次不再询问
        except Exception:  # noqa: BLE001
            pass
    return chosen
