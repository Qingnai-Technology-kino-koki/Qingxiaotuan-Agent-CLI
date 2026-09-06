"""新提取模块的单元测试。

覆盖:
1. AgentModelRouter — 模型路由
2. AgentResilience — 韧性组件
3. AgentObservability — 可观测性
"""
from unittest import mock

import pytest

from qingxiaotuan.config import Config
from qingxiaotuan.core.model_router import AgentModelRouter
from qingxiaotuan.core.resilience import AgentResilience
from qingxiaotuan.core.observability import AgentObservability
from qingxiaotuan.core.retry import RetryPolicy, RateLimiter, CircuitBreaker


# ============================================================ AgentModelRouter


class TestAgentModelRouter:
    """AgentModelRouter 测试。"""

    def test_disabled_router_does_no_switch(self):
        """router 未启用时不做切换 (switch=False)。"""
        config = Config()
        kernel = mock.MagicMock()
        router = AgentModelRouter(config, kernel)
        router.maybe_route("test task")
        # 未配置 router.enabled 时, 可能仍会做路由决策但 switch=False
        if router.last_route is not None:
            assert router.last_route["switch"] is False

    def test_last_route_set_after_decision(self):
        """路由决策后 last_route 被设置。"""
        config = Config()
        config.data = {"router": {"enabled": True, "auto_switch": True},
                       "model": {"provider": "deepseek", "model": "deepseek-chat"}}
        kernel = mock.MagicMock()
        router = AgentModelRouter(config, kernel)
        with mock.patch("qingxiaotuan.models.router.ModelRouter") as MockRouter:
            instance = mock.MagicMock()
            instance.decide.return_value = {"switch": False, "reason": "already optimal"}
            MockRouter.return_value = instance
            MockRouter.available_provider_names.return_value = ["deepseek"]
            router.maybe_route("easy task")
            assert router.last_route is not None
            assert router.last_route["switch"] is False
            assert router.last_route["reason"] == "already optimal"

    def test_backward_compat_switcher(self):
        """_model_switcher 向后兼容。"""
        config = Config()
        kernel = mock.MagicMock()
        router = AgentModelRouter(config, kernel)
        assert router._model_switcher is None
        router._model_switcher = lambda k, o: None
        assert router._model_switcher is not None


# ============================================================ AgentResilience


class TestAgentResilience:
    """AgentResilience 测试。"""

    def test_default_components_created(self):
        """默认配置下创建韧性组件。"""
        config = Config()
        kernel = mock.MagicMock()
        res = AgentResilience(config, kernel)
        assert res.retry_policy is not None
        assert res._rate_limiter is not None
        assert res._circuit_breaker is not None

    def test_status_returns_dict(self):
        """status() 返回完整的状态字典。"""
        config = Config()
        kernel = mock.MagicMock()
        res = AgentResilience(config, kernel)
        status = res.status()
        assert "circuit_breaker" in status
        assert "rate_limiter" in status
        assert "retry" in status

    def test_backward_compat_properties(self):
        """max_retries / retry_backoff / retry_jitter / retry_on 向后兼容。"""
        config = Config()
        kernel = mock.MagicMock()
        res = AgentResilience(config, kernel)
        res.max_retries = 5
        assert res.max_retries == 5
        res.retry_backoff = 3.0
        assert res.retry_backoff == 3.0
        res.retry_jitter = 0.5
        assert res.retry_jitter == 0.5

    def test_chat_with_retry_success(self):
        """chat_with_retry 正常调用成功。"""
        config = Config()
        kernel = mock.MagicMock()
        res = AgentResilience(config, kernel)
        model = mock.MagicMock()
        model.chat.return_value = mock.MagicMock(content="ok")
        result = res.chat_with_retry(model, [{"role": "user", "content": "hi"}], None, False)
        assert result.content == "ok"

    def test_chat_with_retry_retries_on_failure(self):
        """chat_with_retry 在失败时重试。"""
        config = Config()
        config.data = {"agent": {"max_retries": 2, "retry_backoff": 0.0, "retry_jitter": 0.0}}
        kernel = mock.MagicMock()
        res = AgentResilience(config, kernel)
        model = mock.MagicMock()
        model.chat.side_effect = [
            RuntimeError("transient"),
            mock.MagicMock(content="ok"),
        ]
        with mock.patch("qingxiaotuan.core.retry.time.sleep"):
            result = res.chat_with_retry(model, [], None, False)
        assert result.content == "ok"

    def test_disabled_breaker_passthrough(self):
        """禁用的熔断器直通。"""
        config = Config()
        kernel = mock.MagicMock()
        res = AgentResilience(config, kernel)
        assert not res._circuit_breaker.enabled


# ============================================================ AgentObservability


class TestAgentObservability:
    """AgentObservability 测试。"""

    def test_disabled_by_default(self):
        """默认不启用遥测。"""
        config = Config()
        obs = AgentObservability(config)
        assert obs.telemetry is None
        assert obs.trace_id == ""

    def test_accumulate_usage(self):
        """用量累计正常工作。"""
        config = Config()
        obs = AgentObservability(config)
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        obs.accumulate_usage(usage)
        assert obs.total_usage["prompt_tokens"] == 100
        assert obs.total_usage["completion_tokens"] == 50
        # 累加
        obs.accumulate_usage(usage)
        assert obs.total_usage["prompt_tokens"] == 200

    def test_accumulate_usage_with_session_append(self):
        """用量累计同时写入会话流。"""
        config = Config()
        obs = AgentObservability(config)
        session_append = mock.MagicMock()
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        obs.accumulate_usage(usage, session_append=session_append, config=config, turn_count=1)
        session_append.assert_called_once()
        call_args = session_append.call_args
        assert call_args[0][0] == "usage"

    def test_estimate_total_cost(self):
        """成本估算返回浮点数。"""
        config = Config()
        obs = AgentObservability(config)
        obs.total_usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        cost = obs.estimate_total_cost()
        assert isinstance(cost, float)

    def test_cache_hit_rate_no_data(self):
        """无数据时缓存命中率返回 None。"""
        config = Config()
        obs = AgentObservability(config)
        assert obs.cache_hit_rate() is None

    def test_cache_hit_rate_with_data(self):
        """有数据时缓存命中率正常计算。"""
        config = Config()
        obs = AgentObservability(config)
        obs.total_usage = {
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
        }
        rate = obs.cache_hit_rate()
        assert rate == pytest.approx(0.8)

    def test_span_operations_noop_when_disabled(self):
        """遥测关闭时 span 操作为空操作。"""
        config = Config()
        obs = AgentObservability(config)
        span = obs.start_span("test")
        assert span is None
        # 不应抛异常
        obs.finish_span("fake_id")
        obs.record_model_call(100.0, 100)
        obs.record_tool_call("test_tool", 50.0)

    def test_start_finish_trace(self):
        """trace 管理正常工作。"""
        config = Config()
        obs = AgentObservability(config)
        tid = obs.start_trace("test")
        assert tid == ""  # 遥测关闭时返回空
        obs.finish_trace(tid)  # 不应抛异常

    def test_status_dict(self):
        """status() 返回完整的状态字典。"""
        config = Config()
        obs = AgentObservability(config)
        status = obs.status()
        assert "enabled" in status
        assert "trace_id" in status
        assert "total_usage" in status
        assert status["enabled"] is False
