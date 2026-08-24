"""测试: 双运行模式 (standard/yolo) + 新增核心工具 + MCP 桥接 + Loop 增强。"""

from __future__ import annotations

import json
from pathlib import Path

from qingxiaotuan.config import Config
from qingxiaotuan.tools.base import ToolContext, Tool, ToolRegistry
from qingxiaotuan.tools.filesystem import glob, move_file, delete_file, write_file


# ----------------------------------------------------------------- 模式

def test_default_mode_is_standard(qxt_home):
    cfg = Config()
    assert cfg.mode == "standard"
    assert not cfg.is_yolo()
    assert cfg.tool_auto_approve("delete_file") is False


def test_mode_setter_and_yolo(qxt_home):
    cfg = Config()
    cfg.mode = "yolo"
    assert cfg.is_yolo()
    # YOLO 下默认全部自动批准 (红名单为空)
    assert cfg.tool_auto_approve("delete_file") is True
    assert cfg.tool_auto_approve("run_shell") is True
    # 红名单中的工具仍强制确认
    cfg.set_user("mode.yolo_require_confirm", ["delete_file"])
    assert cfg.tool_auto_approve("delete_file") is False
    assert cfg.tool_auto_approve("run_shell") is True


def test_invalid_mode_rejected(qxt_home):
    cfg = Config()
    try:
        cfg.mode = "turbo"
        assert False, "应拒绝非法模式"
    except ValueError:
        pass


# ----------------------------------------------------------------- YOLO 下的 dispatch 自动批准

def _make_ctx(qxt_home, yolo: bool):
    class _FakeKernel:
        def get(self, _):
            return None
    return ToolContext(kernel=_FakeKernel(), workspace=str(qxt_home), yolo=yolo)


def test_dangerous_tool_confirmed_in_standard():
    reg = ToolRegistry()
    calls = []
    reg.register(Tool(name="bomb", description="d", parameters={"type": "object", "properties": {}},
                      handler=lambda ctx: "boom", dangerous=True))
    ctx = _make_ctx(None, yolo=False)
    ctx.confirm = lambda p: False  # 用户拒绝
    out = reg.dispatch("bomb", "{}", ctx)
    assert "已拒绝" in out


def test_dangerous_tool_auto_approved_in_yolo():
    reg = ToolRegistry()
    approved = []
    reg.register(Tool(name="bomb", description="d", parameters={"type": "object", "properties": {}},
                      handler=lambda ctx: "boom", dangerous=True))
    ctx = _make_ctx(None, yolo=True)
    ctx.on_auto_approve = lambda name: approved.append(name)
    out = reg.dispatch("bomb", "{}", ctx)
    assert out == "boom"
    assert approved == ["bomb"]


def test_yolo_redlist_still_confirms():
    reg = ToolRegistry()
    reg.register(Tool(name="bomb", description="d", parameters={"type": "object", "properties": {}},
                      handler=lambda ctx: "boom", dangerous=True, yolo_confirm=True))
    ctx = _make_ctx(None, yolo=True)
    ctx.confirm = lambda p: True  # 红名单仍要确认, 这里批准
    out = reg.dispatch("bomb", "{}", ctx)
    assert out == "boom"


# ----------------------------------------------------------------- 新增文件工具

def test_glob_finds_python_files(tmp_path):
    (tmp_path / "a.py").write_text("x=1")
    (tmp_path / "b.py").write_text("y=2")
    (tmp_path / "c.txt").write_text("z")
    class _K:
        def get(self, _):
            return None
    ctx = ToolContext(kernel=_K(), workspace=str(tmp_path))
    out = glob(ctx, "*.py")
    assert "a.py" in out and "b.py" in out and "c.txt" not in out


def test_move_and_delete_file(tmp_path):
    src = tmp_path / "old.txt"
    src.write_text("hi")
    dst = tmp_path / "sub" / "new.txt"
    class _K:
        def get(self, _):
            return None
    ctx = ToolContext(kernel=_K(), workspace=str(tmp_path))
    assert "已移动" in move_file(ctx, "old.txt", "sub/new.txt")
    assert dst.exists() and not src.exists()
    assert "已删除" in delete_file(ctx, "sub/new.txt")
    assert not dst.exists()


# ----------------------------------------------------------------- MCP 桥接 (用假 client 验证注册与转发)

def test_mcp_tool_bridge():
    reg = ToolRegistry()

    class FakeClient:
        name = "demo"
        def list_tools(self):
            return [{"name": "ping", "description": "pong", "inputSchema": {"type": "object", "properties": {}}}]
        def call_tool(self, name, args):
            return f"called {name} with {args}"

    from qingxiaotuan.tools.mcp.plugin import MCPPlugin
    MCPPlugin._register_tool(reg, FakeClient(), FakeClient().list_tools()[0])
    tool = reg.get("mcp__demo__ping")
    assert tool is not None
    assert tool.description == "pong"
    # 调用转发到远端
    out = reg.dispatch("mcp__demo__ping", json.dumps({"x": 1}),
                       ToolContext(kernel=None, workspace="."))
    assert "called ping" in out


# ----------------------------------------------------------------- Loop 收敛去重

def test_loop_detects_repetition(monkeypatch):
    # 用一个固定返回的假 agent, 连续两轮回复相同, 验证去重提示被注入
    from qingxiaotuan.core.devloop import DevLoop

    class FakeCfg:
        def get(self, k, d=None):
            return {"loop.max_iterations": 3, "loop.ask_every": 0,
                    "loop.auto_test": False, "loop.stop_on_user_ok": False}.get(k, d)
        def is_yolo(self):
            return False

    captured = []
    class FakeAgent:
        def run(self, prompt, **kw):
            captured.append(prompt)
            return "无新进展, 仍在调查。"  # 重复内容
        kernel = type("K", (), {"emit": staticmethod(lambda *a, **k: None)})()

    loop = DevLoop(FakeAgent(), FakeCfg())
    final = loop.run("task", stream=False)
    # 第二轮应注入"重复"系统提醒
    assert any("高度重复" in p for p in captured)
    assert "循环结束" in final
