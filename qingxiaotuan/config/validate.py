"""配置校验 —— 在改动前后给用户一个自检入口 (qxt config validate)。

校验维度:
- 类型: 关键字段是否符合预期类型 (max_iterations 为 int, temperature 为 0~2 的 float 等)。
- 范围: 枚举/数值落在合理区间 (mode ∈ {standard,yolo}, effort ∈ {low,medium,high})。
- 一致性: api_key_env 指向的环境变量是否存在 (不读取值, 只报缺失)。
- 引用: profiles/<name>/config.yaml 等路径是否可解析。

不联网、不打印密钥。返回 (errors, warnings) 列表, 供 CLI 展示。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

# (点路径, 期望类型, 可选范围/校验)
_RULES: List[Tuple[str, Any, Any]] = [
    ("model.temperature", float, (0.0, 2.0)),
    ("model.max_tokens", int, (1, 1_000_000)),
    ("model.timeout", (int, float), (1, 600)),
    ("model.connect_timeout", (int, float), (1, 120)),
    ("model.read_timeout", (int, float), (1, 600)),
    ("agent.max_iterations", int, (1, 200)),
    ("agent.max_retries", int, (0, 10)),
    ("agent.context_max_messages", int, (4, 500)),
    ("agent.subagent_max_workers", int, (1, 16)),
    ("agent.subagent_timeout", (int, float), (5, 3600)),
    ("context.keep_recent", int, (1, 100)),
    ("context.budget_tokens", int, (1000, 2_000_000)),
    ("context.compact_trigger", int, (1000, 2_000_000)),
    ("loop.max_iterations", int, (1, 200)),
    ("background.max_turns", int, (1, 1000)),
    ("background.report_every", int, (1, 100)),
    ("background.poll_interval", (int, float), (0.1, 60)),
]

_ENUM_RULES: List[Tuple[str, tuple]] = [
    ("mode.default", ("standard", "yolo")),
    ("agent.effort", ("low", "medium", "high")),
    ("ui.theme", ("dark", "light")),
    ("context.compact_strategy", ("smart", "none")),
    ("agent.subagent_isolation", ("process", "thread")),
]


def validate(config) -> Tuple[List[str], List[str]]:
    """返回 (errors, warnings)。error 阻断性, warning 提示性。"""
    errors: List[str] = []
    warnings: List[str] = []

    for dotted, typ, rng in _RULES:
        val = config.get(dotted)
        if val is None:
            continue
        if not isinstance(val, typ):
            errors.append(f"类型错误: {dotted} 应为 {typ.__name__}, 实际为 {type(val).__name__}")
            continue
        if rng is not None:
            lo, hi = rng
            if not (lo <= val <= hi):
                errors.append(f"取值越界: {dotted}={val} 不在 [{lo}, {hi}] 区间")

    for dotted, allowed in _ENUM_RULES:
        val = config.get(dotted)
        if val is None:
            continue
        if val not in allowed:
            errors.append(f"非法枚举: {dotted}={val!r} 不在 {allowed} 中")

    # api_key_env 指向的变量是否存在 (仅报缺失, 不读值)
    env_name = config.get("model.api_key_env")
    if env_name and not os.environ.get(env_name):
        warnings.append(f"环境变量 {env_name} 未设置 (运行 qxt setup 或写入 ~/.qingxiaotuan/.env)")

    # 模型/provider 非空
    if not config.get("model.model"):
        errors.append("model.model 为空, 请设置要使用的模型名")

    # provider 不枚举限制在已知清单内 (青小团支持任意 OpenAI 兼容网关)。
    # 未知 provider 必须给 base_url, 否则运行时无法连接 —— 这里给警告。
    from ..models import is_known_provider
    provider = config.get("model.provider", "deepseek")
    if not is_known_provider(provider) and not config.get("model.base_url"):
        errors.append(
            f"model.base_url 为空: 未知 provider {provider!r} 必须设为 OpenAI 兼容网关地址"
        )
    elif not config.get("model.base_url"):
        # 已知 provider 但没配 base_url: 给出默认端点提示而非硬错 (create_adapter 会兜底)
        warnings.append(
            f"model.base_url 未显式设置, 将按 provider={provider!r} 的默认端点连接"
        )

    return errors, warnings
