"""JSON 结构化引擎 (纯 Python) 集成测试。

覆盖:
- pointer: RFC 6901 取值 (嵌套对象/数组)
- diff:    结构化比较两个 JSON, 返回逐路径变更
- merge:   深合并 (overlay 覆盖 base, 递归对象)
- 内核工具 ext_json_pointer / ext_json_diff / ext_json_merge 接线
"""
import json

import pytest

from qingxiaotuan.core.ipc_client import ExternalEngineManager

AVAIL = set(ExternalEngineManager().list_engines())
HAVE = "json" in AVAIL


@pytest.mark.skipif(not HAVE, reason="json 引擎不可用")
def test_pointer_nested():
    m = ExternalEngineManager()
    doc = {"a": {"b": [10, 20, {"c": "hi"}]}}
    r = m.call("json", "pointer_get", {"doc": doc, "pointer": "/a/b/2/c"})
    assert r["value"] == "hi"


@pytest.mark.skipif(not HAVE, reason="json 引擎不可用")
def test_pointer_not_found():
    m = ExternalEngineManager()
    doc = {"x": 1}
    with pytest.raises(Exception):
        m.call("json", "pointer_get", {"doc": doc, "pointer": "/nope"})


@pytest.mark.skipif(not HAVE, reason="json 引擎不可用")
def test_diff_one_change():
    m = ExternalEngineManager()
    a = {"x": 1, "y": 2}
    b = {"x": 1, "y": 99}
    r = m.call("json", "diff", {"base": a, "overlay": b})
    assert r["count"] == 1
    ch = r["changes"][0]
    assert ch["op"] == "replace"
    assert ch["path"] == "/y"


@pytest.mark.skipif(not HAVE, reason="json 引擎不可用")
def test_merge_overlay():
    m = ExternalEngineManager()
    base = {"k1": 1, "k2": {"n": 2}}
    ov = {"k2": {"n": 20}, "k3": 3}
    r = m.call("json", "merge", {"base": base, "overlay": ov})
    merged = r["merged"]
    assert merged["k1"] == 1
    assert merged["k2"]["n"] == 20
    assert merged["k3"] == 3


@pytest.mark.skipif(not HAVE, reason="json 引擎不可用")
def test_kernel_tools_wired():
    from qingxiaotuan.core.kernel import Kernel
    from qingxiaotuan.config.loader import Config
    from qingxiaotuan.config.plugin import ConfigPlugin
    from qingxiaotuan.tools import ToolRegistryPlugin
    from qingxiaotuan.tools.external import ExternalToolsPlugin

    k = Kernel()
    k.register(ConfigPlugin(Config(profile="default")))
    ToolRegistryPlugin().activate(k)
    ExternalToolsPlugin().activate(k)
    reg = k.require("tool_registry")
    names = {t.name for t in reg.tools}
    for n in ("ext_json_pointer", "ext_json_diff", "ext_json_merge"):
        assert n in names
    # 实际调用一次
    res = reg.dispatch("ext_json_pointer",
                       json.dumps({"doc": json.dumps({"u": [{"name": "alice"}]}),
                                   "pointer": "/u/0/name"}),
                       __import__("qingxiaotuan.tools.base", fromlist=["ToolContext"]).ToolContext(kernel=k, workspace="."))
    assert "alice" in res
