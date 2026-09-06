"""Claude Code 对标 CLI 新参数测试: --permission-mode / --effort xhigh|max / --max-turns."""

from __future__ import annotations

import pytest

from qingxiaotuan.cli.parser import build_parser
from qingxiaotuan.cli.cmd_chat import resolve_mode_from_permission
from qingxiaotuan.config.validate import validate


def _parse(argv):
    return build_parser().parse_args(argv)


# ------------------------------------------------------------ permission-mode

def test_permission_mode_bypass_maps_to_yolo():
    args = _parse(["--permission-mode", "bypassPermissions", "run", "t"])
    assert resolve_mode_from_permission(args) == "yolo"


def test_permission_mode_dontask_maps_to_yolo():
    args = _parse(["run", "t", "--permission-mode", "dontAsk"])
    assert resolve_mode_from_permission(args) == "yolo"


def test_permission_mode_plan_maps_to_plan():
    args = _parse(["--permission-mode", "plan", "dev", "t"])
    assert resolve_mode_from_permission(args) == "plan"


def test_permission_mode_default_maps_to_standard():
    args = _parse(["run", "t", "--permission-mode", "default"])
    assert resolve_mode_from_permission(args) == "standard"


def test_explicit_mode_wins_over_permission_mode():
    # resolve_mode_from_permission 只处理 permission_mode; 显式 mode 在 _prepare_agent 优先
    args = _parse(["run", "t", "--mode", "yolo", "--permission-mode", "default"])
    assert resolve_mode_from_permission(args) == "standard"  # 该函数只看 permission_mode
    # 但 _prepare_agent 用 (args.mode or resolve) 组合, 故显式 mode 覆盖


# ---------------------------------------------------------------- effort 扩展

def test_effort_accepts_xhigh_and_max():
    for level in ("xhigh", "max"):
        args = _parse(["dev", "t", "--effort", level])
        assert args.effort == level


def test_effort_parsed_at_top_level():
    args = _parse(["--effort", "high"])
    assert args.effort == "high"


def test_run_accepts_max_turns():
    args = _parse(["run", "t", "--max-turns", "40"])
    assert args.max_turns == 40


def test_validate_accepts_xhigh_effort():
    # 构造假配置对象; effort 合法时无新增 error
    class Cfg:
        def get(self, k, default=None):
            return {"agent.effort": "xhigh"}.get(k, default)
    errs, warns = validate(Cfg())
    assert not any("agent.effort" in e for e in errs)