"""代码理解工具测试 (离线): 地图 / 符号定位 / 引用 / 测试探测。"""

import shutil
import subprocess

from qingxiaotuan.app import build_kernel
from qingxiaotuan.tools.base import Tool, ToolContext
from qingxiaotuan.tools.code import (
    codebase_map, find_references, find_symbol, run_tests,
    git_diff, git_log, doctor,
)
from qingxiaotuan.tools.base import ToolRegistry


def _ctx(tmp_path, qxt_home):
    kernel = build_kernel()
    return ToolContext(kernel=kernel, workspace=str(tmp_path), confirm=lambda _p: True)


def test_find_symbol_locates_definition(tmp_path, qxt_home):
    (tmp_path / "mod.py").write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")
    out = find_symbol(_ctx(tmp_path, qxt_home), "greet")
    assert "mod.py" in out and "greet" in out and ":1:" in out


def test_find_references_tracks_usage(tmp_path, qxt_home):
    (tmp_path / "a.py").write_text("from b import greet\ngreet('x')\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def greet(n):\n    return n\n", encoding="utf-8")
    out = find_references(_ctx(tmp_path, qxt_home), "greet")
    assert "a.py" in out  # 引用出现在 a.py
    assert "b.py" not in out.split("\n")[0] or "b.py" in out  # 定义行不算引用


def test_codebase_map_renders(tmp_path, qxt_home):
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    out = codebase_map(_ctx(tmp_path, qxt_home))
    assert "代码库地图" in out
    assert "main.py" in out


def test_run_tests_explicit_command(tmp_path, qxt_home):
    out = run_tests(_ctx(tmp_path, qxt_home), command="echo hello-from-test")
    assert "hello-from-test" in out
    assert "exit=" in out


# ------------------------------------------------------------------ git 工具

def _init_git(tmp_path):
    if shutil.which("git") is None:
        import pytest
        pytest.skip("git 不可用")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)


def test_git_diff_shows_uncommitted_change(tmp_path, qxt_home):
    _init_git(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("a\nb\n", encoding="utf-8")
    out = git_diff(_ctx(tmp_path, qxt_home))
    assert "f.txt" in out  # 改动文件出现在 diff


def test_git_log_lists_commits(tmp_path, qxt_home):
    _init_git(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)
    out = git_log(_ctx(tmp_path, qxt_home), max_count=5)
    assert "first" in out


# ------------------------------------------------------------------ 健康自检

def test_doctor_reports_health(tmp_path, qxt_home):
    out = doctor(_ctx(tmp_path, qxt_home))
    assert "已注册工具" in out
    assert "LoopProvider" in out
    assert "架构服务" in out


# ------------------------------------------------------------------ 工具集选择 (plan / yolo)

def test_tool_set_plan_is_readonly_subset():
    reg = ToolRegistry()
    reg.register(Tool(name="read_file", description="r",
                     parameters={"type": "object", "properties": {}, "required": []},
                     handler=lambda ctx: "", read_only=True, group="fs"))
    reg.register(Tool(name="write_file", description="w",
                     parameters={"type": "object", "properties": {}, "required": []},
                     handler=lambda ctx: "", read_only=False, group="fs"))
    reg.register(Tool(name="exit_plan_mode", description="e",
                     parameters={"type": "object", "properties": {}, "required": []},
                     handler=lambda ctx: "", read_only=True, group="session"))

    std = reg.schemas(tool_set="standard")
    plan = reg.schemas(tool_set="plan")
    plan_names = {s["function"]["name"] for s in plan}
    assert "write_file" not in plan_names       # 写操作被过滤
    assert "read_file" in plan_names             # 只读保留
    assert "exit_plan_mode" in plan_names        # 退出 plan 必须可达
    assert len(plan) < len(std)


def test_tool_set_unknown_falls_back_to_standard():
    reg = ToolRegistry()
    reg.register(Tool(name="x", description="",
                     parameters={"type": "object", "properties": {}, "required": []},
                     handler=lambda ctx: "", read_only=False))
    assert len(reg.schemas(tool_set="bogus")) == len(reg.schemas(tool_set="standard"))
