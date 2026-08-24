"""safety 引擎 + self-improve 闭环 集成测试。

覆盖:
- C 引擎 safety: score / analyze 风险判定 (最小影响半径)
- self-improve: reflector 抽取经验 / rulegen 生成规则 / skillgen 技能草稿 / 内核插件接入
"""
import json

import pytest

from qingxiaotuan.core.ipc_client import ExternalEngineManager
from qingxiaotuan.core.kernel import Kernel
from qingxiaotuan.config.loader import Config
from qingxiaotuan.config.plugin import ConfigPlugin
from qingxiaotuan.self_improve.plugin import SelfImprovePlugin
from qingxiaotuan.self_improve.reflector import Reflector, Experience
from qingxiaotuan.self_improve.rulegen import RuleGenerator
from qingxiaotuan.self_improve.skillgen import SkillGenerator

AVAIL = set(ExternalEngineManager({}).available())


# ----------------------------------------------------------------- safety 引擎
@pytest.mark.skipif("safety" not in AVAIL, reason="safety 引擎未编译/不可用")
def test_safety_score_critical_block():
    m = ExternalEngineManager({})
    r = m.call("safety", "score", {"command": "git push --force origin main"})
    assert r["risk"] == "critical"
    assert r["block"] is True
    assert any("force push" in x for x in r["reasons"])
    m.close_all()


@pytest.mark.skipif("safety" not in AVAIL, reason="safety 引擎未编译/不可用")
def test_safety_score_safe_none():
    m = ExternalEngineManager({})
    r = m.call("safety", "score", {"command": "ls -la src/"})
    assert r["risk"] == "none"
    assert r["block"] is False
    m.close_all()


@pytest.mark.skipif("safety" not in AVAIL, reason="safety 引擎未编译/不可用")
def test_safety_analyze_overall_advice():
    m = ExternalEngineManager({})
    ops = [
        {"kind": "command", "text": "rm -rf build/"},
        {"kind": "write", "target": "src/app.py", "text": "x=1"},
    ]
    r = m.call("safety", "analyze", {"ops": ops})
    assert r["overall"] == "critical"
    assert r["advice"].startswith("BLOCK")
    kinds = [it["risk"] for it in r["items"]]
    assert "critical" in kinds
    m.close_all()


@pytest.mark.skipif("safety" not in AVAIL, reason="safety 引擎未编译/不可用")
def test_safety_process_stable_across_calls():
    """回归: 曾因 double-free 导致第二个请求时进程退出, 现应稳定长驻。"""
    m = ExternalEngineManager({})
    for i in range(5):
        r = m.call("safety", "score", {"command": "ls" if i % 2 else "DROP TABLE users"})
        assert r["risk"] in ("none", "critical")
    m.close_all()


# ----------------------------------------------------------------- self-improve
def _kernel_with_events():
    k = Kernel()
    k.register(ConfigPlugin(Config(profile="default")))
    k.register(SelfImprovePlugin())
    k.activate_all()
    for _ in range(3):
        k.emit("tool.executed", {"name": "ext_safety_score", "status": "denied"})
    for _ in range(2):
        k.emit("tool.executed", {"name": "ext_crypto_seal", "status": "error", "error_type": "ValueError"})
    for _ in range(6):
        k.emit("tool.executed", {"name": "ext_search", "status": "ok", "elapsed": 0.1})
    return k


def test_reflector_extracts_experiences():
    k = _kernel_with_events()
    exps = Reflector(k).reflect()
    by_kind = {e.kind: e for e in exps}
    assert "denied" in by_kind
    assert "failure" in by_kind
    assert "repeated_ok" in by_kind
    assert by_kind["failure"].detail.get("error_type") == "ValueError"


def test_rulegen_generates_guard_and_lint(tmp_path):
    exps = [
        Experience(kind="denied", tool="ext_safety_score",
                   summary="denied", count=3, detail={"freq": 3}),
        Experience(kind="failure", tool="ext_crypto_seal",
                   summary="fail", count=2, detail={"error_type": "ValueError", "freq": 2}),
    ]
    rg = RuleGenerator(str(tmp_path))
    rules = rg.generate(exps)
    assert len(rules) == 2
    ids = {r["id"] for r in rules}
    assert "self-improve-guard-ext_safety_score" in ids
    assert "self-improve-lint-ext_crypto_seal" in ids
    path = rg.write(rules)
    assert (tmp_path / "self_improve.jsonl").exists()
    lines = (tmp_path / "self_improve.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["meta"]["source"] == "self-improve"


def test_skillgen_draft_created(tmp_path):
    exps = [Experience(kind="repeated_ok", tool="ext_search",
                       summary="ok", count=6, detail={"freq": 6})]
    sg = SkillGenerator(str(tmp_path))
    paths = sg.draft(exps)
    assert len(paths) == 1
    assert (tmp_path / "learned-ext-search" / "SKILL.md").exists()
    content = (tmp_path / "learned-ext-search" / "SKILL.md").read_text(encoding="utf-8")
    assert "status: draft" in content
    assert "ext_search" in content


def test_self_improve_plugin_service():
    k = _kernel_with_events()
    svc = k.get("self_improve")
    assert svc is not None
    summary = svc.summarize()
    assert summary["total_experiences"] >= 3
    applied = svc.apply()
    assert applied["rules_count"] == 2
    assert len(applied["skill_drafts"]) == 1
