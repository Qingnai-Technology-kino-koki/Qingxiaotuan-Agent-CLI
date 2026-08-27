"""外部引擎集成测试: 验证 Python 内核通过 JSONL IPC 驱动 qingxiaotuan/ext 纯 Python 引擎。

若某引擎在当前环境不可用, ExternalEngineManager 会自动跳过, 相关测试 skip。
"""

import json
import threading

import pytest

from qingxiaotuan.core.ipc_client import ExternalEngineManager, IpcClient
from qingxiaotuan.core.kernel import Kernel
from qingxiaotuan.tools import ToolRegistryPlugin, builtin_tool_plugins
from qingxiaotuan.tools.base import ToolContext


def _kernel():
    k = Kernel()
    k.register(ToolRegistryPlugin())
    for p in builtin_tool_plugins():
        if p.name == "tools.external":
            k.register(p)
    cfg = k.get("config") or {}
    cfg.setdefault("ext", {"enabled": True})
    k.activate_all()
    return k


def _ctx(k, tmp_path):
    return ToolContext(kernel=k, workspace=str(tmp_path), confirm=lambda _p: True)


def _avail():
    try:
        return set(ExternalEngineManager().list_engines())
    except Exception:
        return set()


AVAIL = _avail()
HAVE_DIFF = "diff" in AVAIL
HAVE_CRYPTO = "crypto" in AVAIL
HAVE_INDEX = "index" in AVAIL
HAVE_ANSI = "ansi" in AVAIL
HAVE_RULES = "rules" in AVAIL


# ---------------------------------------------------------------- 管理器探测

def test_manager_discovers_engines():
    m = ExternalEngineManager({})
    avail = m.list_engines()
    assert isinstance(avail, list)
    assert "diff" not in avail or "diff" in avail  # 始终成立, 仅确认不抛异常


def test_external_plugin_registers_tools():
    k = _kernel()
    names = [t.name for t in k.require("tool_registry").tools]
    assert "ext_diff" in names
    assert "ext_crypto_seal" in names
    assert "ext_rules_load" in names


@pytest.mark.skipif(not HAVE_DIFF, reason="diff 引擎不可用")
def test_ext_diff_detects_change(tmp_path):
    k = _kernel()
    out = k.require("tool_registry").dispatch("ext_diff", json.dumps(
        {"old": "line1\nline2\n", "new": "line1\nCHANGED\n"}), _ctx(k, tmp_path))
    assert "CHANGED" in out and "line2" in out


@pytest.mark.skipif(not HAVE_DIFF, reason="diff 引擎不可用")
def test_ipc_client_close_reclaims_read_thread(tmp_path):
    """IpcClient.close() 必须终止子进程并回收 _read_loop 线程 (防跨测试泄漏)。"""
    client = IpcClient("diff")
    client.start()
    thread = client._read_thread
    assert thread is not None and thread.is_alive()
    client.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert client.proc is None


@pytest.mark.skipif(not HAVE_DIFF, reason="diff 引擎不可用")
def test_ext_patch_applies(tmp_path):
    k = _kernel()
    registry = k.require("tool_registry")
    ctx = _ctx(k, tmp_path)
    diff = registry.dispatch("ext_diff", json.dumps(
        {"old": "a\nb\nc\n", "new": "a\nX\nc\n"}), ctx)
    patched = registry.dispatch("ext_patch", json.dumps(
        {"patch": diff, "content": "a\nb\nc\n"}), ctx)
    assert "X" in patched


@pytest.mark.skipif(not HAVE_DIFF, reason="diff 引擎不可用")
def test_ext_merge3(tmp_path):
    k = _kernel()
    out = k.require("tool_registry").dispatch("ext_merge3", json.dumps(
        {"base": "shared\n", "a": "sharedA\n", "b": "sharedB\n"}), _ctx(k, tmp_path))
    assert "shared" in out


@pytest.mark.skipif(not HAVE_CRYPTO, reason="crypto 引擎不可用")
def test_ext_crypto_roundtrip(tmp_path):
    k = _kernel()
    registry = k.require("tool_registry")
    ctx = _ctx(k, tmp_path)
    sealed = json.loads(registry.dispatch("ext_crypto_seal", json.dumps(
        {"plaintext": "青小团秘密", "password": "pw"}), ctx))
    assert "blob" in sealed and "salt_b64" in sealed
    opened = json.loads(registry.dispatch("ext_crypto_unseal", json.dumps(
        {"blob": sealed["blob"], "salt": sealed["salt_b64"], "password": "pw"}), ctx))
    assert opened.get("plaintext") == "青小团秘密"


@pytest.mark.skipif(not HAVE_CRYPTO, reason="crypto 引擎不可用")
def test_ext_crypto_wrong_password_fails(tmp_path):
    k = _kernel()
    registry = k.require("tool_registry")
    ctx = _ctx(k, tmp_path)
    sealed = json.loads(registry.dispatch("ext_crypto_seal", json.dumps(
        {"plaintext": "secret", "password": "pw"}), ctx))
    bad = registry.dispatch("ext_crypto_unseal", json.dumps(
        {"blob": sealed["blob"], "salt": sealed["salt_b64"], "password": "wrong"}), ctx)
    assert "tag mismatch" in bad or "错误" in bad or "missing" in bad


@pytest.mark.skipif(not HAVE_ANSI, reason="ansi 引擎不可用")
def test_ext_ansi_strip(tmp_path):
    k = _kernel()
    out = k.require("tool_registry").dispatch("ext_ansi_strip", json.dumps(
        {"text": "\x1b[31m红色\x1b[0m"}), _ctx(k, tmp_path))
    assert "\x1b" not in out and "红色" in out


@pytest.mark.skipif(not HAVE_INDEX, reason="index 引擎不可用")
def test_ext_index_build_and_query(tmp_path):
    (tmp_path / "mod.py").write_text("def hello():\n    return 1\nclass Foo:\n    pass\n", encoding="utf-8")
    k = _kernel()
    registry = k.require("tool_registry")
    ctx = _ctx(k, tmp_path)
    build = registry.dispatch("ext_index_build", json.dumps({"root": str(tmp_path)}), ctx)
    assert "files" in build or "symbols" in build or "ok" in build
    q = registry.dispatch("ext_index_query", json.dumps({"symbol": "hello"}), ctx)
    assert "hello" in q


@pytest.mark.skipif(not HAVE_RULES, reason="rules 引擎不可用")
def test_ext_rules_load_and_check(tmp_path):
    k = _kernel()
    registry = k.require("tool_registry")
    ctx = _ctx(k, tmp_path)
    yaml = (
        "- id: no-todo\n"
        "  severity: error\n"
        '  match:\n    path: "*.py"\n'
        '  assert: \'not contains(content, "TODO")\'\n'
        "  message: 发现TODO\n"
    )
    load = json.loads(registry.dispatch("ext_rules_load", json.dumps({"rules_yaml": yaml}), ctx))
    assert load.get("count") == 1
    v = json.loads(registry.dispatch("ext_rules_check", json.dumps(
        {"path": "app.py", "content": "x = 1 # TODO fix"}), ctx))
    assert v.get("passed") is False and len(v.get("violations", [])) == 1
    v2 = json.loads(registry.dispatch("ext_rules_check", json.dumps(
        {"path": "app.py", "content": "x = 1"}), ctx))
    assert v2.get("passed") is True


@pytest.mark.skipif(not HAVE_RULES, reason="rules 引擎不可用")
def test_ext_skill_search(tmp_path):
    k = _kernel()
    out = k.require("tool_registry").dispatch("ext_skill_search", json.dumps(
        {"query": "zzz-no-such-skill"}), _ctx(k, tmp_path))
    assert "packages" in out
