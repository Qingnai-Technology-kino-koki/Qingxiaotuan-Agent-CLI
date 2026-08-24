"""工具注册表与内置工具测试。"""

import json

from qingxiaotuan.core.kernel import Kernel
from qingxiaotuan.config import Config
from qingxiaotuan.tools import ToolRegistryPlugin, builtin_tool_plugins
from qingxiaotuan.tools.base import ToolContext


def _kernel_with_tools():
    k = Kernel()
    k.provide("config", Config(), owner="test")
    k.register(ToolRegistryPlugin())
    for p in builtin_tool_plugins():
        # memory/skill 工具依赖服务, 这里只挂文件、shell 与 web 工具
        if p.name in ("tools.filesystem", "tools.shell", "tools.web"):
            k.register(p)
    k.activate_all()
    return k


def test_registry_dispatch_and_schema():
    k = _kernel_with_tools()
    registry = k.require("tool_registry")
    names = [t.name for t in registry.tools]
    assert "read_file" in names and "write_file" in names and "web_fetch" in names
    schemas = registry.schemas()
    assert all(s["type"] == "function" for s in schemas)


def test_filesystem_roundtrip(tmp_path):
    k = _kernel_with_tools()
    registry = k.require("tool_registry")
    # 写/改/移动是危险工具, 测试中以确认回调批准来执行
    ctx = ToolContext(kernel=k, workspace=str(tmp_path), confirm=lambda _p: True)

    assert "已写入" in registry.dispatch("write_file", json.dumps(
        {"path": "hello.txt", "content": "你好青小团"}), ctx)
    out = registry.dispatch("read_file", json.dumps({"path": "hello.txt"}), ctx)
    assert "你好青小团" in out

    assert "已修改" in registry.dispatch("edit_file", json.dumps(
        {"path": "hello.txt", "old_string": "青小团", "new_string": "QXT"}), ctx)
    assert "QXT" in registry.dispatch("read_file", json.dumps({"path": "hello.txt"}), ctx)

    tree = registry.dispatch("list_dir", json.dumps({"path": "."}), ctx)
    assert "hello.txt" in tree

    hits = registry.dispatch("search_files", json.dumps({"pattern": "QXT"}), ctx)
    assert "hello.txt" in hits


def test_edit_non_unique_rejected(tmp_path):
    k = _kernel_with_tools()
    registry = k.require("tool_registry")
    ctx = ToolContext(kernel=k, workspace=str(tmp_path), confirm=lambda _p: True)
    (tmp_path / "a.txt").write_text("x x x", encoding="utf-8")
    out = registry.dispatch("edit_file", json.dumps(
        {"path": "a.txt", "old_string": "x", "new_string": "y"}), ctx)
    assert "唯一" in out


def test_filesystem_rejects_workspace_escape(tmp_path):
    k = _kernel_with_tools()
    registry = k.require("tool_registry")
    ctx = ToolContext(kernel=k, workspace=str(tmp_path), confirm=lambda _p: True)
    out = registry.dispatch("write_file", json.dumps(
        {"path": "../outside.txt", "content": "blocked"}), ctx)
    assert "工作区内" in out
    assert not (tmp_path.parent / "outside.txt").exists()


def test_dangerous_tool_denied_without_confirm(tmp_path):
    """危险工具在无确认回调时必须被拒绝 (安全红线)。"""
    k = _kernel_with_tools()
    registry = k.require("tool_registry")
    ctx = ToolContext(kernel=k, workspace=str(tmp_path))  # 无 confirm
    out = registry.dispatch("write_file", json.dumps(
        {"path": "x.txt", "content": "hi"}), ctx)
    assert "已拒绝" in out
    # 非危险工具不受影响
    assert "你好" in registry.dispatch("read_file", json.dumps({"path": "x.txt"}), ctx) or "错误" in registry.dispatch("read_file", json.dumps({"path": "x.txt"}), ctx)


def test_unknown_tool_error():
    k = _kernel_with_tools()
    registry = k.require("tool_registry")
    ctx = ToolContext(kernel=k, workspace=".")
    assert "未知工具" in registry.dispatch("nope", "{}", ctx)


def test_permission_policy_denies_shell_pattern(tmp_path, qxt_home):
    k = _kernel_with_tools()
    config = k.require("config")
    config.data["permissions"]["shell"]["deny_patterns"] = [r"\bformat\b"]
    registry = k.require("tool_registry")
    ctx = ToolContext(kernel=k, workspace=str(tmp_path), confirm=lambda _p: True)
    out = registry.dispatch("run_shell", json.dumps({"command": "format c:"}), ctx)
    assert "拒绝" in out


def test_permission_policy_restricts_network_domain(tmp_path, qxt_home):
    k = _kernel_with_tools()
    config = k.require("config")
    config.data["permissions"]["network"]["allow_domains"] = ["allowed.example"]
    registry = k.require("tool_registry")
    ctx = ToolContext(kernel=k, workspace=str(tmp_path))
    out = registry.dispatch("web_fetch", json.dumps({"url": "https://blocked.example"}), ctx)
    assert "域名" in out


def test_permission_policy_keeps_yolo_redline(tmp_path, qxt_home):
    k = _kernel_with_tools()
    config = k.require("config")
    config.data["mode"]["yolo_require_confirm"] = ["write_file"]
    registry = k.require("tool_registry")
    denied = []
    ctx = ToolContext(kernel=k, workspace=str(tmp_path), yolo=True,
                      confirm=lambda prompt: denied.append(prompt) or False)
    out = registry.dispatch("write_file", json.dumps({"path": "x", "content": "x"}), ctx)
    assert "已拒绝" in out
    assert denied
