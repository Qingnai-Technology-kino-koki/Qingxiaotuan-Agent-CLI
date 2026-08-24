"""性能与成本路由 (Model Router) —— 根据任务难度自动选择模型, 记录每次调用成本。

核心能力:
- 任务难度评估: 根据任务描述/上下文估算难度 (1-10)
- 智能模型选择: 简单任务用便宜模型, 复杂任务用强模型
- 成本追踪: 记录每次调用的 token 用量和估算成本
- 成本报告: 生成会话/任务级别的成本摘要

路由策略:
- difficulty 1-3: 弱模型 (如 deepseek-v4-flash-free)
- difficulty 4-6: 中等模型 (如 deepseek-chat)
- difficulty 7-10: 强模型 (如 deepseek-reasoner / claude)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("qingxiaotuan.router")


@dataclass
class ModelPreset:
    """模型预设: 包含能力等级和成本信息。"""
    provider: str
    model: str
    base_url: str = ""
    api_key_env: str = ""
    tier: int = 1                    # 1=弱(便宜), 2=中, 3=强(贵)
    max_difficulty: int = 3          # 该模型适合的最大任务难度
    cost_per_1k_input: float = 0.0   # 每 1k 输入 token 的成本 (USD)
    cost_per_1k_output: float = 0.0  # 每 1k 输出 token 的成本 (USD)
    capabilities: List[str] = field(default_factory=list)  # 能力标签


# 内置模型预设 —— 按 tier 分层, 覆盖主流供应商
MODEL_PRESETS: List[ModelPreset] = [
    # ---- Tier 1: 免费 / 极低价 (适合简单任务) ----
    ModelPreset(
        provider="opencode-zen", model="deepseek-v4-flash-free",
        base_url="https://opencode.ai/zen/v1",
        api_key_env="OPENCODE_ZEN_API_KEY",
        tier=1, max_difficulty=3,
        cost_per_1k_input=0.0, cost_per_1k_output=0.0,
        capabilities=["chat", "code", "reasoning_basic"],
    ),
    ModelPreset(
        provider="groq", model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        tier=1, max_difficulty=4,
        cost_per_1k_input=0.000059, cost_per_1k_output=0.000079,
        capabilities=["chat", "code", "reasoning", "function_calling"],
    ),
    ModelPreset(
        provider="zhipu", model="glm-4-flash",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="ZHIPU_API_KEY",
        tier=1, max_difficulty=4,
        cost_per_1k_input=0.0, cost_per_1k_output=0.0,
        capabilities=["chat", "code", "reasoning_basic", "function_calling"],
    ),
    ModelPreset(
        provider="doubao", model="doubao-1.5-pro-256k",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env="ARK_API_KEY",
        tier=1, max_difficulty=5,
        cost_per_1k_input=0.00008, cost_per_1k_output=0.00012,
        capabilities=["chat", "code", "reasoning", "function_calling"],
    ),
    ModelPreset(
        provider="qwen", model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        tier=1, max_difficulty=5,
        cost_per_1k_input=0.0002, cost_per_1k_output=0.0006,
        capabilities=["chat", "code", "reasoning", "function_calling"],
    ),
    # ---- Tier 2: 中等 (均衡性价比) ----
    ModelPreset(
        provider="deepseek", model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        tier=2, max_difficulty=6,
        cost_per_1k_input=0.00014, cost_per_1k_output=0.00028,
        capabilities=["chat", "code", "reasoning", "function_calling"],
    ),
    ModelPreset(
        provider="moonshot", model="moonshot-v1-32k",
        base_url="https://api.moonshot.cn/v1",
        api_key_env="MOONSHOT_API_KEY",
        tier=2, max_difficulty=6,
        cost_per_1k_input=0.0008, cost_per_1k_output=0.0008,
        capabilities=["chat", "code", "reasoning", "function_calling"],
    ),
    ModelPreset(
        provider="openai", model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        tier=2, max_difficulty=6,
        cost_per_1k_input=0.00015, cost_per_1k_output=0.0006,
        capabilities=["chat", "code", "reasoning", "function_calling", "vision"],
    ),
    ModelPreset(
        provider="mistral", model="mistral-small-latest",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        tier=2, max_difficulty=6,
        cost_per_1k_input=0.0002, cost_per_1k_output=0.0006,
        capabilities=["chat", "code", "reasoning", "function_calling"],
    ),
    ModelPreset(
        provider="gemini", model="gemini-2.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GEMINI_API_KEY",
        tier=2, max_difficulty=7,
        cost_per_1k_input=0.00015, cost_per_1k_output=0.0006,
        capabilities=["chat", "code", "reasoning", "function_calling", "vision"],
    ),
    ModelPreset(
        provider="anthropic", model="claude-sonnet-4-20250514",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        tier=2, max_difficulty=8,
        cost_per_1k_input=0.003, cost_per_1k_output=0.015,
        capabilities=["chat", "code", "reasoning", "function_calling", "vision"],
    ),
    # ---- Tier 3: 强模型 (复杂任务) ----
    ModelPreset(
        provider="deepseek", model="deepseek-reasoner",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        tier=3, max_difficulty=10,
        cost_per_1k_input=0.00055, cost_per_1k_output=0.00219,
        capabilities=["chat", "code", "reasoning", "function_calling", "extended_thinking"],
    ),
    ModelPreset(
        provider="openai", model="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        tier=3, max_difficulty=10,
        cost_per_1k_input=0.0025, cost_per_1k_output=0.01,
        capabilities=["chat", "code", "reasoning", "function_calling", "vision"],
    ),
    ModelPreset(
        provider="xai", model="grok-3",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        tier=3, max_difficulty=10,
        cost_per_1k_input=0.003, cost_per_1k_output=0.015,
        capabilities=["chat", "code", "reasoning", "function_calling", "vision"],
    ),
    ModelPreset(
        provider="anthropic", model="claude-3-opus-20240229",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        tier=3, max_difficulty=10,
        cost_per_1k_input=0.015, cost_per_1k_output=0.075,
        capabilities=["chat", "code", "reasoning", "function_calling", "vision"],
    ),
]


@dataclass
class CostRecord:
    """单次调用的成本记录。"""
    timestamp: float
    provider: str
    model: str
    task_difficulty: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration: float = 0.0
    cached_tokens: int = 0


class CostTracker:
    """成本追踪器: 记录并汇总所有模型调用的成本。"""

    def __init__(self) -> None:
        self._records: List[CostRecord] = []
        self._session_start = time.time()

    def record(
        self,
        provider: str,
        model: str,
        task_difficulty: int,
        usage: Dict[str, int],
        duration: float = 0.0,
    ) -> CostRecord:
        """记录一次模型调用的成本。"""
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cached_tokens = usage.get("prompt_cache_hit_tokens", 0)

        # 查找模型预设获取成本
        cost = self._estimate_cost(provider, model, prompt_tokens, completion_tokens)

        rec = CostRecord(
            timestamp=time.time(),
            provider=provider,
            model=model,
            task_difficulty=task_difficulty,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            duration=duration,
            cached_tokens=cached_tokens,
        )
        self._records.append(rec)
        return rec

    def _estimate_cost(
        self, provider: str, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """估算调用成本 (USD)。"""
        for preset in MODEL_PRESETS:
            if preset.provider == provider and preset.model == model:
                cost = (prompt_tokens / 1000 * preset.cost_per_1k_input +
                        completion_tokens / 1000 * preset.cost_per_1k_output)
                return round(cost, 6)
        # 未知模型, 给一个粗略估算
        return round((prompt_tokens + completion_tokens) / 1000 * 0.001, 6)

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self._records)

    @property
    def total_tokens(self) -> Dict[str, int]:
        return {
            "prompt": sum(r.prompt_tokens for r in self._records),
            "completion": sum(r.completion_tokens for r in self._records),
            "cached": sum(r.cached_tokens for r in self._records),
        }

    @property
    def records(self) -> List[CostRecord]:
        return list(self._records)

    def summary(self) -> str:
        """生成成本摘要报告。"""
        if not self._records:
            return "暂无调用记录"

        tokens = self.total_tokens
        elapsed = time.time() - self._session_start
        models_used = set(f"{r.provider}/{r.model}" for r in self._records)

        lines = [
            "成本报告",
            "=" * 40,
            f"会话时长: {elapsed:.0f}s",
            f"调用次数: {len(self._records)}",
            f"使用模型: {', '.join(models_used)}",
            f"总 token: 入 {tokens['prompt']:,} / 出 {tokens['completion']:,} / 缓存 {tokens['cached']:,}",
            f"总成本:   ${self.total_cost:.4f}",
            "",
        ]

        # 按模型分组统计
        by_model: Dict[str, List[CostRecord]] = {}
        for r in self._records:
            key = f"{r.provider}/{r.model}"
            by_model.setdefault(key, []).append(r)

        lines.append("按模型统计:")
        for model_key, recs in by_model.items():
            model_cost = sum(r.cost_usd for r in recs)
            model_prompt = sum(r.prompt_tokens for r in recs)
            model_completion = sum(r.completion_tokens for r in recs)
            lines.append(f"  {model_key}: {len(recs)} 次, "
                        f"入 {model_prompt:,} / 出 {model_completion:,}, "
                        f"${model_cost:.4f}")

        # 按难度分布
        diff_counts: Dict[int, int] = {}
        for r in self._records:
            diff_counts[r.task_difficulty] = diff_counts.get(r.task_difficulty, 0) + 1
        if diff_counts:
            lines.append("\n任务难度分布:")
            for diff in sorted(diff_counts):
                lines.append(f"  难度 {diff}: {diff_counts[diff]} 次")

        return "\n".join(lines)


class ModelRouter:
    """智能模型路由器: 根据任务难度自动选择模型。

    路由策略:
    1. 根据任务描述估算难度 (1-10)
    2. 从可用模型中选择适合该难度的最便宜模型
    3. 记录选择和成本
    """

    def __init__(
        self,
        default_provider: str = "deepseek",
        default_model: str = "deepseek-chat",
        budget_limit: float = 0.0,  # 0 = 无限制
    ) -> None:
        self.default_provider = default_provider
        self.default_model = default_model
        self.budget_limit = budget_limit
        self.cost_tracker = CostTracker()
        self._available_presets = list(MODEL_PRESETS)

    def estimate_difficulty(self, task: str, context: str = "") -> int:
        """估算任务难度 (1-10)。

        基于关键词和特征的启发式评估:
        - 简单任务: 查询、总结、格式化
        - 中等任务: 修改、重构、测试
        - 复杂任务: 架构设计、安全审计、性能优化
        """
        text = (task + " " + context).lower()
        score = 5  # 默认中等

        # 简单任务关键词 (-2~3)
        easy_keywords = ["查询", "总结", "列出", "查看", "显示", "格式化",
                         "search", "list", "show", "summary", "format", "read"]
        for kw in easy_keywords:
            if kw in text:
                score -= 1

        # 复杂任务关键词 (+2~4)
        hard_keywords = ["架构", "设计", "重构", "优化", "安全", "审计",
                         "性能", "并发", "分布式", "加密", "认证",
                         "architecture", "design", "refactor", "optimize",
                         "security", "audit", "performance", "concurrent"]
        for kw in hard_keywords:
            if kw in text:
                score += 1

        # 中等任务关键词
        medium_keywords = ["修改", "添加", "实现", "修复", "测试", "集成",
                           "modify", "add", "implement", "fix", "test", "integrate"]
        for kw in medium_keywords:
            if kw in text:
                score += 0.5

        # 任务长度也是难度指标
        if len(task) > 200:
            score += 1
        if len(task) > 500:
            score += 1

        # 代码相关任务通常更难
        code_keywords = ["函数", "类", "模块", "接口", "API", "数据库",
                         "function", "class", "module", "interface", "database"]
        for kw in code_keywords:
            if kw in text:
                score += 0.5

        return max(1, min(10, int(round(score))))

    def select_model(
        self,
        difficulty: int,
        available_providers: Optional[List[str]] = None,
        required_capabilities: Optional[List[str]] = None,
    ) -> Tuple[str, str]:
        """根据难度选择最合适的模型。

        Args:
            difficulty: 任务难度 (1-10)
            available_providers: 可用的供应商列表
            required_capabilities: 必须具备的能力

        Returns:
            (provider, model) 元组
        """
        candidates: List[ModelPreset] = []

        for preset in self._available_presets:
            # 检查供应商是否可用
            if available_providers and preset.provider not in available_providers:
                continue
            # 检查能力是否满足
            if required_capabilities:
                if not all(cap in preset.capabilities for cap in required_capabilities):
                    continue
            # 检查是否适合该难度
            if preset.max_difficulty >= difficulty:
                candidates.append(preset)

        if not candidates:
            # 没有合适的模型, 返回默认
            return self.default_provider, self.default_model

        # 选择最便宜的合适模型
        candidates.sort(key=lambda p: (p.tier, p.cost_per_1k_input + p.cost_per_1k_output))
        best = candidates[0]

        log.info("模型路由: 难度=%d → %s/%s (tier=%d)", difficulty, best.provider, best.model, best.tier)
        return best.provider, best.model

    def should_escalate(self, current_cost: float) -> bool:
        """检查是否应该升级模型 (预算超限或任务太难)。"""
        if self.budget_limit > 0 and current_cost >= self.budget_limit * 0.8:
            return True
        return False

    def route(
        self,
        task: str,
        context: str = "",
        available_providers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """完整的路由流程: 估算难度 → 选择模型 → 返回路由决策。"""
        difficulty = self.estimate_difficulty(task, context)
        provider, model = self.select_model(difficulty, available_providers)

        return {
            "difficulty": difficulty,
            "provider": provider,
            "model": model,
            "reason": self._explain_selection(difficulty, provider, model),
            "cost_estimate": self._estimate_session_cost(difficulty, provider, model),
        }

    def _explain_selection(self, difficulty: int, provider: str, model: str) -> str:
        """解释模型选择原因。"""
        if difficulty <= 3:
            return f"简单任务 (难度 {difficulty}), 选用经济模型节省成本"
        elif difficulty <= 6:
            return f"中等任务 (难度 {difficulty}), 选用均衡模型"
        else:
            return f"复杂任务 (难度 {difficulty}), 选用强力模型确保质量"

    def _estimate_session_cost(self, difficulty: int, provider: str, model: str) -> str:
        """估算单次会话的预期成本。"""
        for preset in self._available_presets:
            if preset.provider == provider and preset.model == model:
                # 粗略估算: 简单任务 ~1k token, 中等 ~5k, 复杂 ~10k
                est_tokens = {1: 1000, 2: 2000, 3: 3000}.get(
                    (difficulty + 2) // 3, 5000
                )
                cost = est_tokens / 1000 * (preset.cost_per_1k_input + preset.cost_per_1k_output)
                if cost == 0:
                    return "免费"
                return f"~${cost:.4f}"
        return "未知"


# ---- 工具函数 ----

def model_route(
    ctx: ToolContext,
    task: str = "",
    context: str = "",
) -> str:
    """智能路由: 根据任务自动选择最合适的模型。

    Args:
        task: 任务描述
        context: 额外上下文
    """
    if not task:
        return "[错误] 请提供任务描述"

    config = ctx.kernel.get("config")
    router = ModelRouter(
        default_provider=config.get("model.provider", "deepseek") if config else "deepseek",
        default_model=config.get("model.model", "deepseek-chat") if config else "deepseek-chat",
    )

    result = router.route(task, context)
    lines = [
        f"任务难度: {result['difficulty']}/10",
        f"推荐模型: {result['provider']}/{result['model']}",
        f"选择理由: {result['reason']}",
        f"预期成本: {result['cost_estimate']}",
        "",
        "如需使用推荐模型, 运行:",
        f"  /model switch {result['provider']} {result['model']}",
    ]
    return "\n".join(lines)


def cost_report(ctx: ToolContext) -> str:
    """生成成本报告。"""
    # 从内核获取或创建 cost tracker
    tracker = ctx.kernel.get("cost_tracker")
    if tracker is None:
        return "暂无成本记录"
    return tracker.summary()
