"""补齐的 Claude Code 对齐斜杠命令: /permissions /status /budget /checkpoint /web /subagent + qxt usercmd。"""

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.base import ModelAdapter, ModelResponse


class MockModel(ModelAdapter):
    name = "mock"

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls.append(messages)
        resp = self.script.pop(0)
        if stream and resp.content and on_token:
            on_token(resp.content)
        return resp


def _build_agent(tmp_path, qxt_home, script=()):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", MockModel(script), owner="test")
    return Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)


def test_slash_permissions_lists_rules(tmp_path, qxt_home, capsys):
    """补丁: /permissions 只读展示权限规则。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    config.data["permissions"]["rules"] = [{"tool": "run_shell", "action": "deny", "pattern": "rm"}]
    config.data["permissions"]["shell"]["deny_patterns"] = [r"\brm\b"]
    config.data["permissions"]["network"]["allow_domains"] = ["example.com"]
    assert _handle_slash("/permissions", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "权限规则" in out
    assert "run_shell" in out
    assert "rm" in out
    assert "example.com" in out


def test_slash_status_summary(tmp_path, qxt_home, capsys):
    """/status 展示模型/模式/上下文摘要。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    assert _handle_slash("/status", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "模型" in out
    assert "模式" in out
    assert "上下文" in out


def test_slash_status_resilience_default_disabled(tmp_path, qxt_home, capsys):
    """/status 默认展示韧性三道防线均为未启用。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    assert _handle_slash("/status", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "韧性" in out
    assert "熔断" in out and "未启用" in out
    assert "限流" in out and "未启用" in out
    assert "重试" in out
    # 默认未启用时不暴露任何熔断细节
    assert "open" not in out and "已熔断" not in out


def test_slash_status_resilience_breaker_closed_and_open(tmp_path, qxt_home, capsys):
    """/status 在熔断器开启时展示 closed (正常) 与 open (已熔断) 两种态。"""
    from qingxiaotuan.cli.commands import _handle_slash
    from qingxiaotuan.core.retry import CircuitBreaker
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    # 启用熔断 (默认已 closed)
    agent._circuit_breaker = CircuitBreaker(
        failure_threshold=3, cooldown=20, success_threshold=1, enabled=True)
    assert _handle_slash("/status", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "已启用" in out and "正常" in out and "closed" in out
    # 手动熔断 -> open
    agent._circuit_breaker.trip()
    assert _handle_slash("/status", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "已熔断" in out and "open" in out


def test_slash_budget_view_and_set(tmp_path, qxt_home, capsys):
    """/budget 无参查看, 有参写入 router.budget_limit。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    assert _handle_slash("/budget", agent, config, str(tmp_path)) is True
    assert "预算" in capsys.readouterr().out
    assert _handle_slash("/budget 0.75", agent, config, str(tmp_path)) is True
    assert float(config.get("router.budget_limit", 0.0)) == 0.75
    # 非法输入给出用法提示
    assert _handle_slash("/budget abc", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "用法" in out


def test_slash_checkpoint_save_list_restore(tmp_path, qxt_home, capsys):
    """/checkpoint save → restore 不抛异常且给出反馈。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    assert _handle_slash("/checkpoint", agent, config, str(tmp_path)) is True
    assert "没有" in capsys.readouterr().out
    assert _handle_slash("/checkpoint save 准备大改", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "检查点 cp1" in out
    assert _handle_slash("/checkpoint", agent, config, str(tmp_path)) is True
    assert "cp1" in capsys.readouterr().out
    assert _handle_slash("/checkpoint restore", agent, config, str(tmp_path)) is True
    assert "检查点 cp1" in capsys.readouterr().out


def test_slash_web_and_subagent_usage(tmp_path, qxt_home, capsys):
    """/web 和 /subagent 无参给出用法提示, 不发起网络/子代理。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    assert _handle_slash("/web", agent, config, str(tmp_path)) is True
    assert "用法" in capsys.readouterr().out
    assert _handle_slash("/subagent", agent, config, str(tmp_path)) is True
    assert "用法" in capsys.readouterr().out


def test_help_lists_new_commands(tmp_path, qxt_home, capsys):
    """/help 帮助文本包含新补齐的命令条目。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    assert _handle_slash("/help", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    for token in ("/permissions", "/status", "/budget", "/checkpoint", "/web", "/subagent"):
        assert token in out


def test_parser_registers_usercmd():
    """qxt usercmd [list] 子命令注册且分发到 cmd_usercmd。"""
    from qingxiaotuan.cli.parser import build_parser
    parser = build_parser()
    ns = parser.parse_args(["usercmd", "list"])
    assert ns.func == "cmd_usercmd"
    assert ns.usercmd_cmd == "list"


def _build_agent_telemetry(tmp_path, qxt_home):
    """构造一个开启 observability.telemetry 的 Agent (其余配置同 _build_agent)。"""
    kernel = build_kernel()
    config = kernel.require("config")
    config.data.setdefault("observability", {})["telemetry"] = {"enabled": True}
    config.data["agent"]["skill_nudge_interval"] = 999  # 测试中不许技能蒸馏打断 run()
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", MockModel(()), owner="test")
    return Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)


def test_agent_creates_telemetry_when_enabled(tmp_path, qxt_home):
    """observability.telemetry.enabled=true 时, Agent 自动创建 TelemetryCollector。"""
    agent = _build_agent_telemetry(tmp_path, qxt_home)
    assert agent._telemetry is not None
    assert agent._telemetry_trace  # 会话级 trace_id 已生成


def test_agent_no_telemetry_by_default(tmp_path, qxt_home):
    """默认不开启 telemetry, Agent._telemetry 为 None (零行为影响)。"""
    agent = _build_agent(tmp_path, qxt_home)
    assert agent._telemetry is None


def test_slash_stats_disabled_message(tmp_path, qxt_home, capsys):
    """未启用 telemetry 时, /stats 提示如何开启而非报错。"""
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    assert _handle_slash("/stats", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "未启用" in out
    assert "observability.telemetry.enabled" in out


def test_slash_stats_shows_run_metrics(tmp_path, qxt_home, capsys):
    """开启 telemetry 后跑一轮, /stats 能展示累积的 span 与指标。"""
    from qingxiaotuan.cli.commands import _handle_slash
    from qingxiaotuan.models.base import ModelResponse
    agent = _build_agent_telemetry(tmp_path, qxt_home)
    config = agent.config
    # 一次带 usage 的模型响应, 无 tool_calls → run 一轮即返回
    agent.model.script = [ModelResponse(
        content="done", tool_calls=[], usage={"prompt_tokens": 12, "completion_tokens": 6})]
    answer = agent.run("hi")
    assert answer == "done"
    # telemetry 已收集到至少一次模型调用 span
    stats = agent._telemetry.get_stats()
    assert stats["total_spans"] >= 1
    assert stats["total_traces"] >= 1

    assert _handle_slash("/stats", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "可观测性" in out
    assert "模型调用" in out
    assert "工具调用" in out