"""自动技能蒸馏闭环测试: 事件流 → 经验 → 技能 → 反馈。"""
import time

from qingxiaotuan.core.kernel import Kernel
from qingxiaotuan.config.loader import Config
from qingxiaotuan.config.plugin import ConfigPlugin
from qingxiaotuan.self_improve.plugin import SelfImprovePlugin


class _FakeSessionStore:
    """最小会话存储桩: 仅暴露 distiller 需要的 read_all。"""

    def __init__(self, records):
        self._records = records

    def read_all(self, limit=None):
        return self._records


def _ok_records(tool: str, n: int):
    return [{"type": "tool.executed", "name": tool, "status": "ok",
             "elapsed": 0.1, "ts": time.time()} for _ in range(n)]


def _build_kernel(tmp_path, records, cfg_patch=None):
    k = Kernel()
    cfg = Config(profile="default")
    cfg.home = tmp_path  # 运行时状态全部落到 tmp, 不污染用户主目录
    if cfg_patch:
        cfg.get = lambda key, default=None, _p=cfg_patch: _p.get(key, default)
    k.register(ConfigPlugin(cfg))
    k.register(SelfImprovePlugin())
    k.provide("session_store", _FakeSessionStore(records), owner="test")
    k.activate_all()
    return k, cfg


def test_distill_closed_loop(tmp_path):
    """高频成功工具应被蒸馏为技能并登记。"""
    records = _ok_records("read_file", 5)
    k, cfg = _build_kernel(tmp_path, records)
    svc = k.get("self_improve")
    assert svc is not None

    out = svc.distill()
    assert out["enabled"] is True
    names = out["new_skills"]
    assert any("learned-read" in n for n in names)
    # 蒸馏技能已落盘到 <home>/skills/distilled
    assert (tmp_path / "skills" / "distilled").is_dir()


def test_distill_feedback_promotes_draft(tmp_path):
    """草稿技能经连续成功反馈后转正 (auto_activate=False 场景)。"""
    records = _ok_records("search", 5)
    k, cfg = _build_kernel(tmp_path, records, cfg_patch={
        "self_improve.auto_activate": False,
    })
    svc = k.get("self_improve")

    out = svc.distill()
    names = out["new_skills"]
    assert names  # 有蒸馏技能, 但处于 draft (auto_activate=False)
    name = names[0]

    # 连续成功反馈 → 转正
    for _ in range(3):
        assert svc.feedback(name, True) is True
    skills = {s["name"]: s for s in svc.distilled_skills()}
    assert skills[name]["active"] is True


def test_distill_feedback_degrades_on_failure(tmp_path):
    """失败反馈降低置信度, 跌破阈值后技能停用。"""
    records = _ok_records("edit", 5)
    k, cfg = _build_kernel(tmp_path, records)
    svc = k.get("self_improve")
    out = svc.distill()
    assert out["new_skills"]
    name = out["new_skills"][0]

    # 连续失败 → 置信度跌破 0.2 → 停用 (初始 1.0, 每次 -0.1, 需 9 次)
    for _ in range(9):
        svc.feedback(name, False)
    skills = {s["name"]: s for s in svc.distilled_skills()}
    assert skills[name]["active"] is False


def test_apply_wires_distill_step(tmp_path):
    """apply() 应在固化规则后顺带触发蒸馏 (闭环同批落成)。"""
    records = _ok_records("grep", 5)
    k, cfg = _build_kernel(tmp_path, records)
    svc = k.get("self_improve")
    data = svc.apply()
    assert "distilled" in data  # 蒸馏闭环已接线
    assert data["distilled"]["enabled"] is True
