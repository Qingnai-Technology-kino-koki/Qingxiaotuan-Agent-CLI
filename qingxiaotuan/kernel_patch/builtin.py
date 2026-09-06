"""内置内核补丁 —— 内置在"内核补丁层"里、开箱即用的可回滚增强。

命名/优先级:
  x.i18n.*   命中国际化热路径 (i18n.t 高频取词)
  x.retry.*  命中重试分类热路径 (classify_error 每次重复 import)
  三者均为 fail-open capability 补丁, 失败退回原实现, 不改变任何对外语义。

回滚: KernelPatchManager.revert_all() 或 revert("<name>") 完全还原现场。
"""

from __future__ import annotations

from .patch_base import patch_impl, SKIP_ORIGINAL

# 本模块的补丁共享缓存: 由补丁实现读写, 不污染 i18n / retry 模块命名空间。
_T_CACHE: dict = {}
_T_CACHE_LANG: str = ""
_T_RAW_CACHE: dict = {}   # (lang, key) -> raw_text


# =================================================================== i18n.t 记忆化

@patch_impl("qingxiaotuan.i18n.t", priority=1, version="1.0.0", min_kernel="0.2.0")
def _i18n_t_memo(original, key, **kw):
    """热路径翻译记忆化: 无插值 (纯取词) 场景直接查缓存返回原始模板。

    纯函数: 结果仅取决于 (当前语言, key)。语言切换由 _i18n_lang_clear 清缓存,
    插值场景走原逻辑 (模板 + kwargs 现场 format), 保证与未补丁时逐字节一致。
    """
    global _T_CACHE_LANG, _T_RAW_CACHE
    from ..i18n import get_language
    lang = get_language()
    if not kw:
        # 纯取词: 记忆 key -> 文本
        cache = _T_RAW_CACHE.get(lang)
        if cache is not None:
            hit = cache.get(key, _MISS)
            if hit is not _MISS:
                return hit
        out = original(key)
        cache = _T_RAW_CACHE.setdefault(lang, {})
        cache[key] = out
        return out
    # 插值场景: 频率低, 直接走原逻辑 (模板缓存命中也仅省一次 dict 查询, 价值不大)
    return original(key, **kw)


# 哨兵: 与任何真实文本区隔 (真实翻译文本不会是一个具体对象实例)
_MISS = object()


@patch_impl("qingxiaotuan.i18n.set_language", priority=1, version="1.0.0",
            min_kernel="0.2.0")
def _i18n_lang_clear(original, lang):
    """语言切换时清空翻译缓存, 保证记忆化结果不串语种。"""
    _T_RAW_CACHE.clear()
    return original(lang)


# =================================================================== retry 导入缓存

_RETRY_ADAPTER_CACHE: list = []   # [adapter|None]; 空表表示"尚未解析"


@patch_impl("qingxiaotuan.core.retry.classify_error", priority=1, version="1.0.0",
            min_kernel="0.2.0", mode="replace")
def _retry_classify_import_cache(exc):
    """消除 classify_error 每次调用都 try/except 重复 import 的开销。

    与原始逻辑逐字节等价, 仅把 `from ..models.openai_compat import ...`
    从每次调用提升为模块级一次性导入 (失败缓存 None, 不再反复尝试)。
    """
    if not _RETRY_ADAPTER_CACHE:
        _RETRY_ADAPTER_CACHE.append(_resolve_adapter())
    adapter = _RETRY_ADAPTER_CACHE[0]
    if adapter is None:
        return "other", None
    if hasattr(exc, "status_code") or "openai" in type(exc).__module__.lower():
        return adapter.classify_error(exc)
    return "other", None


def _resolve_adapter():
    try:
        from ..models.openai_compat import OpenAICompatAdapter
        return OpenAICompatAdapter
    except Exception:  # noqa: BLE001
        return None


# =================================================================== 版本锚定演示补丁
# 该补丁要求内核 >= 9.x, 当前为 0.2.x → 会被版本检查跳过并广播 patch.bypass。
# 它用于演示"版本感知跳过"能力, 不作为实际生效补丁。

@patch_impl("qingxiaotuan.core.retry.retry_after_seconds", priority=999,
            version="9.9.9", min_kernel="9.0.0")
def _demo_always_bypassed(original, exc):
    return original(exc)