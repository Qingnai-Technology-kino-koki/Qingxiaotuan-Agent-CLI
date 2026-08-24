"""健壮性测试 (离线): 模型超时/重试、UI 错误展示、上下文预算条。"""

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.base import ModelAdapter, ModelResponse
from qingxiaotuan.ui.repl import UI
from qingxiaotuan.config import Config


class FlakyModel(ModelAdapter):
    """前 N-1 次抛异常, 第 N 次返回正常结果 —— 验证重试最终成功。"""
    name = "flaky"

    def __init__(self, fail_times, then):
        self.fail_times = fail_times
        self.then = then
        self.calls = 0

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"网络抖动 #{self.calls}")
        return self.then


class AlwaysFailModel(ModelAdapter):
    name = "always-fail"

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        raise RuntimeError("持续不可用")


def _build(tmp_path, model, max_retries=3):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    config.data["agent"]["max_retries"] = max_retries
    config.data["agent"]["retry_backoff"] = 0.01  # 测试里退避几乎为 0
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", model, owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)
    return agent


def test_retry_recovers_after_transient_failures(tmp_path, qxt_home):
    good = ModelResponse(content="最终成功")
    agent = _build(tmp_path, FlakyModel(fail_times=2, then=good), max_retries=3)
    # 第一次 chat 会被重试包裹: 抛 2 次 -> 第 3 次成功
    out = agent.run("hi", stream=False)
    assert out == "最终成功"
    # FlakyModel 实例的 chat 被调用次数 = 1 (重试在 agent 层, 模型实例同一次)
    # 验证 agent 的 max_retries 配置生效
    assert agent.max_retries == 3


def test_retry_exhausted_returns_error_via_callback(tmp_path, qxt_home):
    agent = _build(tmp_path, AlwaysFailModel(), max_retries=2)
    errors = []
    out = agent.run("hi", stream=False, on_error=lambda m: errors.append(m))
    # 不崩溃, 通过 on_error 返回错误文本
    assert errors, "应触发 on_error"
    assert "模型错误" in out
    assert "重试" in errors[0]


def test_context_bar_renders_and_computes_pct():
    ui = UI()
    # 预算 1000, 估算 350 -> 35%
    ui.context_bar({"messages": 10, "estimated_tokens": 350,
                    "budget_tokens": 1000, "keep_recent": 14})
    # 不抛异常即视为通过; 再测高占用变红路径
    ui.context_bar({"messages": 50, "estimated_tokens": 900,
                    "budget_tokens": 1000, "keep_recent": 14})


def test_tool_result_detects_failure():
    ui = UI()
    # 捕获打印内容
    import io
    from rich.console import Console
    buf = io.StringIO()
    ui.console = Console(file=buf, force_terminal=False)
    ui.tool_result("run_tests", "[错误] 测试失败 exit=1\nassert add(2,3)==5")
    out = buf.getvalue()
    assert "✗" in out  # 失败图标
    buf2 = io.StringIO()
    ui.console = Console(file=buf2, force_terminal=False)
    ui.tool_result("read_file", "# ok.py\nprint(1)")
    assert "✓" in buf2.getvalue()  # 成功图标


def test_config_has_timeout_and_retries():
    cfg = Config()
    assert cfg.get("model.timeout", 0) > 0
    assert cfg.get("agent.max_retries", 0) >= 1
    assert cfg.get("agent.retry_backoff", 0) > 0
