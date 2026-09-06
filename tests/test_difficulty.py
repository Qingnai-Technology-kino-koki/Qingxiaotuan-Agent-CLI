# -*- coding: utf-8 -*-
"""DifficultyClassifier 单测 —— 覆盖评审 Major #4 的对抗性绕过与回归。"""
import pytest

from qingxiaotuan.models.difficulty import DifficultyClassifier, estimate_difficulty
from qingxiaotuan.models.router import ModelRouter


@pytest.fixture
def clf():
    return DifficultyClassifier()


@pytest.fixture
def router():
    return ModelRouter(default_provider="deepseek", default_model="deepseek-chat")


# ---------------------------------------------------------------- 对抗性前缀消解
@pytest.mark.parametrize("cmd,expected_min", [
    # 释放词 + 强结构信号 → 不得被拉低 (旧实现会抵消成 medium)
    ("简单总结一下这个复杂架构设计", 6),
    ("quickly summarize the distributed architecture migration", 6),
    ("大概说一下这个微服务系统的接口对接和并发处理", 6),
    ("简略看看数据库事务与缓存一致性的设计", 6),
    # 分布式强一致/共识类: 属高复杂度结构信号, 不得被低估
    ("设计一个分布式强一致性共识算法并证明正确性, 考虑网络分区与脑裂", 7),
])
def test_release_word_cannot_hide_structural_complexity(clf, cmd, expected_min):
    d = clf.classify(cmd)
    assert d >= expected_min, f"{cmd!r} -> {d}, 期望 >= {expected_min}"


def test_pure_easy_task_still_low(clf):
    # 真正简单、无结构信号的任务不应被抬升
    assert clf.classify("查询今天的天气") <= 4
    assert clf.classify("printf hello world") <= 5


def test_pure_structural_task_hard(clf):
    # 纯结构任务直接给高难度
    assert clf.classify("设计分布式系统的架构并做安全审计") >= 8


def test_deterministic(clf):
    # 同一输入两次结果一致 (无随机)
    s = "重构微服务网关并纳入并发限流"
    assert clf.classify(s) == clf.classify(s)


# ---------------------------------------------------------------- 与旧 API 兼容
def test_estimate_difficulty_module_level_and_router(router):
    # 模块级便捷入口与 ModelRouter 委托均返回 1-10
    for fn in (estimate_difficulty, router.estimate_difficulty):
        for probe in ("查询天气", "设计整体架构并优化性能"):
            v = fn(probe)
            assert 1 <= v <= 10


def test_router_adversarial_no_longer_mid(router):
    # 关键回归: "简单" + "架构设计" 不得消解回中等
    d = router.estimate_difficulty("简单总结一下这个复杂架构设计")
    assert d >= 6