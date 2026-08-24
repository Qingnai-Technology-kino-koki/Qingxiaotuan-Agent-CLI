"""Self-Improve 规则实时闭环测试。

覆盖:
- RuleGenerator 生成带 action 字段的规则
- SelfImproveRuleStore 落盘/加载/查询
- ToolRegistry 分发前查询 learned 规则, 命中则升级为需确认 (不静默阻断)
- 闭环: reflector 经验 -> apply() 写入 store -> 下一次 dispatch 命中
"""
import json

import pytest

from qingxiaotuan.core.kernel import Kernel
from qingxiaotuan.config.loader import Config
from qingxiaotuan.config.plugin import ConfigPlugin
from qingxiaotuan.tools import ToolRegistryPlugin
from qingxiaotuan.tools.base import ToolContext, Tool, PermissionPolicy
from qingxiaotuan.self_improve.rulegen import RuleGenerator
from qingxiaotuan.self_improve.store import SelfImproveRuleStore
from qingxiaotuan.self_improve.reflector import Experience


def _tmp_jsonl(tmp_path):
    return str(tmp_path / "self_improve.jsonl")


def test_rulegen_has_action():
    exps = [Experience(kind="denied", tool="run_shell", count=3, summary="被拒经验", detail={})]
    rules = RuleGenerator("/tmp").generate(exps)
    assert rules
    assert rules[0]["action"] == "confirm"
    assert rules[0]["match"]["tool"] == "run_shell"


def test_store_roundtrip_and_query(tmp_path):
    p = _tmp_jsonl(tmp_path)
    store = SelfImproveRuleStore(p)
    store.write([
        {"id": "g1", "severity": "high", "action": "confirm",
         "match": {"tool": "run_shell"}, "message": "历史上被拒 3 次",
         "meta": {"source": "self-improve"}},
    ])
    assert store.count() == 1
    # 重新加载
    s2 = SelfImproveRuleStore(p)
    assert s2.load() == 1
    hit = s2.query("run_shell", {"command": "rm -rf x"})
    assert hit is not None
    assert hit["message"] == "历史上被拒 3 次"
    # 未命中
    assert s2.query("read_file", {}) is None


def test_dispatch_upgrades_on_learned_rule(tmp_path):
    """learned 规则命中的工具调用, 自动升级为需确认。"""
    from qingxiaotuan.tools.base import ToolResult

    p = _tmp_jsonl(tmp_path)
    store = SelfImproveRuleStore(p)
    store.write([
        {"id": "g1", "severity": "high", "action": "confirm",
         "match": {"tool": "run_shell"}, "message": "历史拒绝经验",
         "meta": {"source": "self-improve"}},
    ])

    k = Kernel()
    k.register(ConfigPlugin(Config(profile="default")))
    ToolRegistryPlugin().activate(k)
    k.provide("self_improve_rules", store, owner="test")

    reg = k.require("tool_registry")
    reg.register(Tool(name="run_shell", description="x", parameters={"type": "object"},
                      handler=lambda ctx, **a: "ok-output", group="shell"))

    # 模拟用户确认被拒绝
    ctx = ToolContext(kernel=k, workspace=".", confirm=lambda msg: False)
    res = reg.dispatch_result("run_shell", json.dumps({"command": "rm -rf x"}), ctx)
    assert isinstance(res, ToolResult)
    assert res.status == "denied"
    assert "learned" in res.content  # 因 learned 护栏升级为确认后用户拒绝

    # 用户确认通过 -> 执行
    ctx2 = ToolContext(kernel=k, workspace=".", confirm=lambda msg: True)
    res2 = reg.dispatch_result("run_shell", json.dumps({"command": "rm -rf x"}), ctx2)
    assert res2.status == "ok"
    assert res2.content == "ok-output"


def test_dispatch_no_learned_no_block(tmp_path):
    """无 learned 规则时, 非危险工具直接执行不要求确认。"""
    k = Kernel()
    k.register(ConfigPlugin(Config(profile="default")))
    ToolRegistryPlugin().activate(k)
    store = SelfImproveRuleStore(_tmp_jsonl(tmp_path))
    store.load()  # 空
    k.provide("self_improve_rules", store, owner="test")

    reg = k.require("tool_registry")
    reg.register(Tool(name="read_file", description="x", parameters={"type": "object"},
                      handler=lambda ctx, **a: "file-content", group="fs"))
    ctx = ToolContext(kernel=k, workspace=".")
    res = reg.dispatch_result("read_file", json.dumps({"path": "a.txt"}), ctx)
    assert res.status == "ok"
    assert res.content == "file-content"
