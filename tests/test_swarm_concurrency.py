"""验证 /swarm 的 n_hint 真正约束 worker 并发度。"""

import pytest

from qingxiaotuan.core.swarm import Swarm
from qingxiaotuan.core.markers import DONE_MARKERS, is_done


class _FakeConfig:
    """最小配置桩: 只实现 .get。"""

    def __init__(self, data):
        self._d = data

    def get(self, key, default=None):
        # 支持一级/二级 dotted
        parts = key.split(".")
        cur = self._d
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return default
        return cur


def _cfg(subagent_max_workers=4):
    return _FakeConfig({"agent": {"subagent_max_workers": subagent_max_workers}})


def test_n_hint_derives_max_workers():
    """未显式给 max_workers 时, max_workers 应取 min(n_hint, 配置默认)。"""
    s = Swarm(kernel=None, config=_cfg(4), workspace="/tmp", n_hint=3)
    assert s.max_workers == 3  # min(3, 4)


def test_n_hint_clamped_to_default_max():
    """n_hint 超过配置默认时, 用配置默认 (不无限放大并发)。"""
    s = Swarm(kernel=None, config=_cfg(4), workspace="/tmp", n_hint=8)
    assert s.max_workers == 4  # min(8, 4)


def test_explicit_max_workers_wins():
    """显式 max_workers 时不被 n_hint 覆盖。"""
    s = Swarm(kernel=None, config=_cfg(4), workspace="/tmp", n_hint=3, max_workers=2)
    assert s.max_workers == 2


def test_n_hint_clamped_bounds():
    """n_hint 越界时夹到 [2, 8]。"""
    assert Swarm(kernel=None, config=_cfg(), workspace="/tmp", n_hint=1).n_hint == 2
    assert Swarm(kernel=None, config=_cfg(), workspace="/tmp", n_hint=99).n_hint == 8


# ---------------------------------------------------------------- done markers


def test_is_done_uses_shared_markers():
    assert is_done("...【已完成】任务做完") is True
    assert is_done("✅ 完成") is True
    assert is_done("功能已完成, 交付如下") is True  # background 之前漏掉的标记
    assert is_done("DONE: all good") is True
    assert is_done("TASK_DONE") is True
    assert is_done("还在做, 没完") is False
    assert is_done("") is False


def test_done_markers_set():
    assert "功能已完成" in DONE_MARKERS
    assert "【已完成】" in DONE_MARKERS
    assert len(DONE_MARKERS) == 6
