"""qxt codedev —— 代码开发子系统命令行入口。

子命令：
    qxt codedev demo      造一个临时项目, 端到端演示 检索→验证→分解→编排
    qxt codedev doctor    检查子系统健康度
    qxt codedev retrieve <task> [--root .] [--top-k 8]   检索相关代码上下文
    qxt codedev verify   [--cwd .]                        运行验证闸门
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from ..codedev import CodeDevEngine


def _tmp_project(root: str) -> None:
    """造一个最小可检索/可验证的项目, 供 demo 使用。"""
    pkg = Path(root) / "myproject"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "auth.py").write_text(
        '"""用户认证模块。"""\n'
        '\n'
        'SESSION_KEY = "sid"\n'
        '\n'
        'def login(user: str, password: str) -> str:\n'
        '    """校验凭据并返回会话 token。"""\n'
        '    if not user or not password:\n'
        '        raise ValueError("empty creds")\n'
        '    return f"token-{user}"\n'
        '\n'
        'def logout(token: str) -> bool:\n'
        '    """使会话失效。"""\n'
        '    return bool(token)\n'
        '\n'
        'def register(email: str, password: str) -> str:\n'
        '    """创建新用户。"""\n'
        '    if "@" not in email:\n'
        '        raise ValueError("bad email")\n'
        '    return login(email, password)\n',
        encoding="utf-8",
    )
    tests = Path(root) / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_auth.py").write_text(
        'from myproject.auth import login, logout\n'
        '\n'
        'def test_login():\n'
        '    assert login("a", "b").startswith("token-")\n'
        '\n'
        'def test_logout():\n'
        '    assert logout("x") is True\n',
        encoding="utf-8",
    )
    (Path(root) / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")


def _cmd_demo() -> int:
    tmp = tempfile.mkdtemp(prefix="qxt-codedev-demo-")
    _tmp_project(tmp)
    print(f"[codedev demo] 临时项目: {tmp}\n")
    engine = CodeDevEngine(workspace=tmp)

    task = "实现用户登录功能并补充测试"
    print("=== 1) 检索上下文 ===")
    res = engine.forge_context(task, top_k=6)
    print(res.pack()[:1500])
    print(f"\n→ 命中 {len(res.hits)} 个相关符号, 扫描 {res.files_scanned} 文件, "
          f"耗时 {res.elapsed_ms:.0f}ms\n")

    print("=== 2) 规格分解 ===")
    spec = engine.plan(task, top_k=6, max_subtasks=4)
    print(spec.plan_md + "\n")

    print("=== 3) 验证闸门 ===")
    report = engine.verify(tmp)
    print(report.summary())
    print(f"→ 检测器: {', '.join(report.ran) or '无 (环境未装)'}")

    print("=== 4) 编排开发（确定性演练, 不拉起真实模型）===")
    dev = engine.develop(task, cwd=tmp, decompose=True, verify=True, max_subtasks=4)
    print(f"→ 成本倍数 ≈ {dev.cost_factor:.1f}x（预算 {dev.cost_budget_pct:.0f}%, "
          f"{'内' if dev.within_budget else '超→回退'}）")
    print(f"→ 子代理摘要: {len(dev.subagent_summaries)} 条")
    print(f"→ 技能蒸馏:\n{dev.skill[:400]}")

    ok = len(res.hits) > 0 and len(spec.subtasks) >= 2
    print("\n[codedev demo] " + ("全部通过 ✅" if ok else "部分未通过 ❌"))
    return 0 if ok else 1


def _cmd_doctor() -> int:
    engine = CodeDevEngine(workspace=os.getcwd())
    d = engine.doctor()
    print("=== codedev 健康度 ===")
    for k, v in d.items():
        print(f"  {k}: {v}")
    return 0


def _cmd_retrieve(args) -> int:
    engine = CodeDevEngine(workspace=args.root)
    res = engine.forge_context(args.task, top_k=getattr(args, "top_k", 8))
    print(res.pack())
    return 0


def _cmd_verify(args) -> int:
    engine = CodeDevEngine(workspace=args.cwd)
    report = engine.verify(args.cwd)
    print(report.summary())
    return 0


def cmd_codedev(args) -> int:
    sub = getattr(args, "codedev_cmd", None)
    if sub == "demo":
        return _cmd_demo()
    if sub == "doctor":
        return _cmd_doctor()
    if sub == "retrieve":
        return _cmd_retrieve(args)
    if sub == "verify":
        return _cmd_verify(args)
    print("未知子命令; 可用: demo / doctor / retrieve / verify", file=sys.stderr)
    return 2
