"""融合层 Agent loop 控制原语测试 (fusion.agent_loop_enhanced)。

覆盖: 瞬时错误分类、retry_step 重试瞬时/不重试非瞬时/全失败上抛、resolve_max_iterations
续跑上限 (关闭等价原生 / 开启用更高上限)、should_continue 谓词。无需外部 LLM/网络。
"""

from __future__ import annotations

import pytest

from qingxiaotuan.core.agent_loop_fusion import (
    classify_transient,
    resolve_max_iterations,
    retry_step,
    should_continue,
)


def _cfg(**kw):
    # 真实 config 以「点号」嵌套键存储 (如 fusion.agent_loop_enhanced); 关键字参数只能含
    # 下划线, 这里把 fusion_ 前缀的首个下划线转成点号, 对齐真实 config 的键名。
    return {
        (k.replace("_", ".", 1) if k.startswith("fusion_") else k): v
        for k, v in kw.items()
    }


# ---------------------------------------------------------------- 瞬时错误分类


def test_classify_transient_network():
    assert classify_transient(ConnectionError("connection reset by peer")) is True
    assert classify_transient(TimeoutError("read timeout")) is True


def test_classify_transient_non_transient():
    assert classify_transient(ValueError("bad arguments")) is False
    assert classify_transient(RuntimeError("unexpected")) is False


def test_classify_transient_by_keyword():
    assert classify_transient(Exception("HTTP 429 rate limit exceeded")) is True
    assert classify_transient(Exception("upstream 503 unavailable")) is True


# ---------------------------------------------------------------- retry_step


def test_retry_step_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert retry_step(flaky, max_retries=3) == "ok"
    assert calls["n"] == 3


def test_retry_step_does_not_retry_non_transient():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("fatal")

    with pytest.raises(ValueError):
        retry_step(boom, max_retries=3)
    assert calls["n"] == 1  # 非瞬时错误立即上抛, 不重试


def test_retry_step_reraises_after_exhaustion():
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise TimeoutError("transient but persistent")

    with pytest.raises(TimeoutError):
        retry_step(always, max_retries=2)
    assert calls["n"] == 2


# ---------------------------------------------------------------- 续跑上限


def test_resolve_max_iterations_default_is_40():
    cfg = _cfg(fusion_agent_loop_max_iterations=40)
    assert resolve_max_iterations(cfg, None) == 40  # 默认 40
    assert resolve_max_iterations(cfg, 5) == 5       # explicit 优先


def test_resolve_max_iterations_custom_cap():
    cfg = _cfg(fusion_agent_loop_max_iterations=60)
    assert resolve_max_iterations(cfg, None) == 60
    # explicit 仍优先于默认值
    assert resolve_max_iterations(cfg, 7) == 7


def test_should_continue():
    assert should_continue(0, 5) is True
    assert should_continue(4, 5) is True
    assert should_continue(5, 5) is False
