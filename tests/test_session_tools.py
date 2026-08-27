"""会话交互工具测试 (todo / ask_user / plan-mode 工具化)。

验证:
- 五个工具注册进内核;
- todo_write 全量覆盖语义 + 状态校验 (非法 status / 多个 in_progress 拒绝);
- todo_read 渲染与进度统计;
- ask_user: headless 安全降级 vs 交互终端读答案 (编号映射);
- enter/exit_plan_mode: agent 与 ctx 双同步, dispatch 层真实拦截修改类工具,
  exit 必须附上实施计划文本。
"""

import io

from qingxiaotuan.app import build_kernel
from qingxiaotuan.core.agent import Agent


def _make_ctx(tmp_path):
    kernel = build_kernel()
    config = kernel.require("config")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda _p: True)
    return kernel, agent


# ------------------------------------------------------------------ 注册

def test_session_tools_registered(qxt_home):
    kernel = build_kernel()
    names = {t.name for t in kernel.require("tool_registry").tools}
    for n in ("todo_write", "todo_read", "ask_user",
              "enter_plan_mode", "exit_plan_mode"):
        assert n in names, f"缺少工具 {n}"


# ------------------------------------------------------------------ todo

def test_todo_write_and_read_roundtrip(tmp_path, qxt_home):
    kernel, agent = _make_ctx(tmp_path)
    reg = kernel.require("tool_registry")
    out = reg.get("todo_write").handler(agent.ctx, todos=[
        {"content": "调研代码", "status": "completed"},
        {"content": "写实现", "status": "in_progress"},
        {"content": "补测试", "status": "pending"},
    ])
    assert "1/3" in out
    listing = reg.get("todo_read").handler(agent.ctx)
    assert "☑" in listing and "◔" in listing and "☐" in listing
    assert "进度: 1/3" in listing
    # ctx 上可取回结构化数据 (全量覆盖语义)
    assert [t["content"] for t in agent.ctx.todos] == ["调研代码", "写实现", "补测试"]


def test_todo_write_validation(tmp_path, qxt_home):
    kernel, agent = _make_ctx(tmp_path)
    reg = kernel.require("tool_registry")
    # 空 todos 拒绝
    assert "错误" in reg.get("todo_write").handler(agent.ctx, todos=[])
    # 非法 status 拒绝且不落盘
    out = reg.get("todo_write").handler(
        agent.ctx, todos=[{"content": "x", "status": "doing"}])
    assert "错误" in out and "doing" in out
    assert agent.ctx.todos is None
    # 同时两个 in_progress 拒绝
    out2 = reg.get("todo_write").handler(agent.ctx, todos=[
        {"content": "a", "status": "in_progress"},
        {"content": "b", "status": "in_progress"},
    ])
    assert "错误" in out2 and agent.ctx.todos is None


# ------------------------------------------------------------------ ask_user

def test_ask_user_headless_fallback(tmp_path, qxt_home, monkeypatch):
    """非交互环境: 不提问, 安全降级并把问题/选项带回给模型。"""
    kernel, agent = _make_ctx(tmp_path)
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    out = kernel.require("tool_registry").get("ask_user").handler(
        agent.ctx, question="用哪种方案?", options=["A", "B"])
    assert "无法交互" in out and "用哪种方案?" in out and "A | B" in out


def test_ask_user_interactive_choice(tmp_path, qxt_home, monkeypatch):
    """交互终端: 输入编号自动映射到选项文本。"""
    kernel, agent = _make_ctx(tmp_path)
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _p="": "2")
    out = kernel.require("tool_registry").get("ask_user").handler(
        agent.ctx, question="选哪个?", options=["方案甲", "方案乙"])
    assert out == "用户回答: 方案乙"
    # 自由作答原样返回
    monkeypatch.setattr("builtins.input", lambda _p="": "都行, 听你的")
    out2 = kernel.require("tool_registry").get("ask_user").handler(
        agent.ctx, question="补充说明?")
    assert out2 == "用户回答: 都行, 听你的"


def test_ask_user_skipped_returns_hint(tmp_path, qxt_home, monkeypatch):
    """用户 Ctrl+C 跳过提问: 返回明确提示而非崩溃。"""
    kernel, agent = _make_ctx(tmp_path)
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def _raise(_p=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _raise)
    out = kernel.require("tool_registry").get("ask_user").handler(
        agent.ctx, question="在吗?")
    assert "跳过" in out


# ------------------------------------------------------------------ plan mode 工具化

def test_plan_mode_tools_toggle_and_block(tmp_path, qxt_home):
    kernel, agent = _make_ctx(tmp_path)
    reg = kernel.require("tool_registry")
    assert not agent.plan_mode and not agent.ctx.plan_mode

    out = reg.get("enter_plan_mode").handler(agent.ctx)
    assert "Plan 模式已开启" in out
    # 双同步: agent 与 ctx 一致
    assert agent.plan_mode and agent.ctx.plan_mode

    # dispatch 层真实拦截: Plan 下写文件被拒绝
    import json as _json
    result = reg.dispatch_result(
        "write_file",
        _json.dumps({"path": "plan_blocked.txt", "content": "x"}),
        agent.ctx,
    )
    assert result.status == "denied" and "Plan" in result.content
    assert not (tmp_path / "plan_blocked.txt").exists()

    # exit 必须附上计划
    bad = reg.get("exit_plan_mode").handler(agent.ctx, plan="")
    assert "错误" in bad and agent.ctx.plan_mode
    ok = reg.get("exit_plan_mode").handler(agent.ctx, plan="1. 改 a.py\n2. 补测试")
    assert "Plan 模式已关闭" in ok and "实施计划" in ok
    assert not agent.plan_mode and not agent.ctx.plan_mode
    # 关闭后同一写入放行
    result2 = reg.dispatch_result(
        "write_file",
        _json.dumps({"path": "plan_ok.txt", "content": "y"}),
        agent.ctx,
    )
    assert result2.status == "ok"
