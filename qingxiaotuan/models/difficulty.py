# -*- coding: utf-8 -*-
"""轻量任务难度分类器 —— 替代纯关键词加权的朴素启发式。

历史缺陷 (评审 Critical→Major #4): 旧的 ``estimate_difficulty`` 是「子串出现即加/减
固定分」的朴素加法, 极易被提示词工程绕过。典型绕过:

    "简单总结一下这个复杂架构设计"

旧实现里 "简单"(easy) 与 "架构/设计"(hard) 同时命中, 朴素加法把两者抵消成
medium, 而模型真实要面对的是 high 复杂度的架构梳理任务。

本分类器把难度判定拆成三层结构化特征, 并做「对抗性前缀消解」:

1. 消解词 (简单/quick/briefly/tldr/summarize briefly…) 只能降低*表达层*的期望,
   不能掩盖*目标本身*的结构化复杂度 —— 当命令含多步骤/多模块/并发/分布式/
   schema/接口对接等强结构信号时, 消解词被降级为「范围限定」而非「难度减半」。
2. 强结构信号 (≥2 处) 直接抬高难度下限, 从根上杜绝「先说简单再叠加复杂」的绕过。
3. 其余按关键词频次、任务长度、代码实体密度加权, 输出 1-10 的稳定整数。

纯确定性、零第三方依赖、可单测; 接口刻意与 ``ModelRouter.estimate_difficulty``
对齐 (输入 task+context, 输出 1-10), 便于后续替换成真正的 fasttext/sklearn/
小模型分类器而无须改动调用方。
"""
from __future__ import annotations

import re
from typing import List, Tuple


# ---------------------------------------------------------------- 特征词表

# 强结构信号: 命中即意味着任务必须处理组件/并发/数据模型/多子系统交互。
# 这类信号权重最高, 且会抬升难度下限, 消解词不能将其抵消。
_STRONG_STRUCTURE = [
    "架构", "分布式", "并发", "异步", "多线程", "微服务", "数据库", "schema",
    "索引", "事务", "集群", "负载均衡", "队列", "缓存一致性", "接口对接",
    "协议", "序列化", "权限模型", "认证", "加密", "审计", "容灾", "迁移",
    "强一致性", "弱一致性", "分布式一致性", "共识算法", "一致性协议", "网络分区",
    "脑裂", "分区容错", "raft", "paxos",
    "architecture", "distributed", "concurrency", "microservice", "database",
    "transaction", "cluster", "load balanc", "message queue", "auth",
    "encryption", "consistency", "schema", "protocol", "failover",
]

# 多步骤/规划类动词: 任务需要拆阶段落地, 而非单点改动。
_MULTI_STEP = [
    "规划", "设计", "实现", "重构", "优化", "落地", "部署", "迁移", "集成",
    "plan", "design", "implement", "refactor", "optimi", "migrat", "integrat",
    "deploy", "architect",
]

# 消解词: 表达层「只要个大概」, 但不得掩盖结构复杂度。
_RELEASE_WORDS = [
    "简单", "简略", "大概", "概括", "概述", "总结一下", "大致", "quick", "brief",
    "briefly", "tl;dr", "tldr", "summarize", "just", "simply", "overview",
]

# 关键词复杂度底座 (延续旧词表, 用于无强结构信号时的加权)
_EASY_WORDS = [
    "查询", "列出", "查看", "显示", "格式化", "换算", "search", "list", "show",
    "format", "read", "convert",
]
_MEDIUM_WORDS = [
    "修改", "添加", "修复", "测试", "改", "新增", "modify", "add", "fix",
    "test", "change", "update",
]
_HARD_WORDS = [
    "性能", "安全", "加密", "认证", "审计", "并发", "分布式", "调优", "加固",
    "security", "perform", "optim", "audit", "harden",
]
_CODE_WORDS = [
    "函数", "类", "模块", "接口", "API", "数据库", "函数签名", "类型",
    "function", "class", "module", "interface", "api", "database", "type",
]

# "简单/quick" 等前置修饰 + 至少一个强结构或规划信号 → 判定为对抗性缩略。
# 注意: CJK 之间没有词边界, 不能用 \\b 包裹中文词 (否则永远不匹配)。
_RELEASE_TOKENS = re.compile(
    r"简单|简略|概括|概述|大概|大致|粗略|简要|quick|brief(?:ly)?|tl;? ?dr|summarize|just|simply",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- 实现

def _kw_count(text: str, words: List[str]) -> int:
    """按出现频次累计命中数 (子串匹配, 支持中英混排)。"""
    n = 0
    for kw in words:
        n += text.count(kw)
    return n


class DifficultyClassifier:
    """确定性任务难度分类器。"""

    # 强结构信号 ≥ 该数时, 难度下限直接抬到 high 档
    STRONG_STRUCTURE_LIFT_THRESHOLD = 1

    def classify(self, task: str, context: str = "") -> int:
        """返回 1-10 的整型难度。"""
        text = (task + " " + context).lower()
        easy = _kw_count(text, _EASY_WORDS)
        medium = _kw_count(text, _MEDIUM_WORDS)
        hard = _kw_count(text, _HARD_WORDS)
        code = _kw_count(text, _CODE_WORDS)
        strong = _kw_count(text, _STRONG_STRUCTURE)
        multi = _kw_count(text, _MULTI_STEP)
        released = bool(_RELEASE_TOKENS.search(task))

        # 正向复杂度驱动项。
        # 基线取低 (1.5): 无任何复杂度信号的例行任务应落在 1-3 档, 由
        # plan_execute 等阶段的 override (2=便宜 / 10=强) 接管规划/执行分档;
        # 基线若抬到 5.0, 会把 "实现一个函数并写测试" 这类无强结构信号的
        # 普通开发任务误判为 7 档, 导致初始路由就落在中高端无法降级省钱。
        score = 1.5
        score += strong * 1.3          # 组件/并发/分布式/schema: 权重最高
        score += multi * 0.7           # 需拆阶段落地的规划类动词
        score += hard * 0.8
        score += medium * 0.5
        score += code * 0.4
        score += min(3.0, len(task) / 200.0)

        # 负向项: 简洁任务词会拉低难度
        score -= easy * 0.6
        # 释放词 (简单/quick/大概…) 只在无结构信号时拉低表达期望
        if released and strong == 0:
            score -= 1.0

        diff = int(max(1.0, min(10.0, round(score))))
        # 强结构信号抬高难度下限: 命中结构词至少 medium-high (≥6)
        if strong >= self.STRONG_STRUCTURE_LIFT_THRESHOLD:
            diff = max(diff, 6)
        return max(1, min(10, diff))


# 模块级单例, 便于无状态复用
_default_classifier = DifficultyClassifier()


def estimate_difficulty(task: str, context: str = "") -> int:
    """便捷入口: 兼容旧的 ``estimate_difficulty(task, context) -> int`` 签名。"""
    return _default_classifier.classify(task, context)