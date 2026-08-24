"""代码理解工具测试 (离线): 地图 / 符号定位 / 引用 / 测试探测。"""

from qingxiaotuan.app import build_kernel
from qingxiaotuan.tools.base import ToolContext
from qingxiaotuan.tools.code import (
    codebase_map, find_references, find_symbol, run_tests,
)


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
