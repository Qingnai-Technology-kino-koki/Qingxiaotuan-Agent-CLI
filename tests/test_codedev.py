"""代码开发子系统测试（离线、确定性、无需真实模型）。"""

import pytest

from qingxiaotuan.codedev import (
    CodeDevEngine, CodeIndex, Verifier, Decomposer, DevSpec,
)
from qingxiaotuan.codedev.verify import _parse, Diagnostic
from qingxiaotuan.app import build_kernel
from qingxiaotuan.tools.base import ToolContext


def _make_project(root):
    (root / "myproject").mkdir(parents=True, exist_ok=True)
    (root / "myproject" / "__init__.py").write_text("", encoding="utf-8")
    (root / "myproject" / "auth.py").write_text(
        '"""用户认证。"""\n'
        'SESSION_KEY = "sid"\n'
        'def login(user, password):\n'
        '    """校验凭据并返回 token。"""\n'
        '    if not user:\n'
        '        raise ValueError("empty")\n'
        '    return f"token-{user}"\n'
        'def logout(token):\n'
        '    """使会话失效。"""\n'
        '    return bool(token)\n',
        encoding="utf-8",
    )
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_auth.py").write_text(
        "from myproject.auth import login\n"
        "def test_login():\n"
        "    assert login('a','b').startswith('token-')\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------ 检索
def test_retrieval_finds_login_by_english(tmp_path):
    _make_project(tmp_path)
    idx = CodeIndex().build(tmp_path)
    res = idx.retrieve("implement login function", top_k=5)
    names = {h.symbol.name for h in res.hits}
    assert "login" in names
    assert res.files_scanned >= 2


def test_retrieval_finds_login_by_chinese(tmp_path):
    _make_project(tmp_path)
    idx = CodeIndex().build(tmp_path)
    res = idx.retrieve("实现登录功能", top_k=5)
    names = {h.symbol.name for h in res.hits}
    assert "login" in names, f"中文检索未命中 login, 命中: {names}"


def test_retrieval_neighbors_present(tmp_path):
    _make_project(tmp_path)
    idx = CodeIndex().build(tmp_path)
    res = idx.retrieve("logout session", top_k=3)
    assert res.hits
    # logout 与 login 在同一文件且互相引用, 应出现邻居
    hit = next(h for h in res.hits if h.symbol.name == "logout")
    assert any(n.name == "login" for n in hit.neighbors)


def test_retrieval_pack_renders(tmp_path):
    _make_project(tmp_path)
    idx = CodeIndex().build(tmp_path)
    res = idx.retrieve("login", top_k=3)
    pack = res.pack()
    assert "auth.py" in pack and "login" in pack


# ------------------------------------------------------------------ 验证
def test_verify_parser_ruff():
    out = "myproject/auth.py:3:1: F401 'os' imported but unused\n"
    diags = _parse("ruff", out)
    assert len(diags) == 1
    d = diags[0]
    assert d.file == "myproject/auth.py" and d.line == 3 and d.code == "F401"
    assert "未使用" in d.hint


def test_verify_parser_mypy():
    out = "x.py:10:5: error: Incompatible types (str vs int)  [assignment]\n"
    diags = _parse("mypy", out)
    assert diags and diags[0].severity == "error" and diags[0].line == 10


def test_verifier_no_detectors_is_safe(tmp_path):
    # 空目录无检测标记 → 不应抛异常, 返回空诊断
    report = Verifier().verify(tmp_path)
    assert report.diagnostics == []
    assert isinstance(report.ran, list)


def test_verifier_available_runs():
    # 不应抛异常（环境可能没有 ruff, 返回空列表即可）
    assert isinstance(Verifier.available(), list)


# ------------------------------------------------------------------ 分解
def test_decompose_produces_subtasks(tmp_path):
    _make_project(tmp_path)
    idx = CodeIndex().build(tmp_path)
    res = idx.retrieve("实现登录功能", top_k=6)
    spec = Decomposer(max_subtasks=4).decompose("实现登录功能", res)
    assert isinstance(spec, DevSpec)
    assert len(spec.subtasks) >= 2
    assert any(st.id == "tests" for st in spec.subtasks)
    assert any(st.id == "verify" for st in spec.subtasks)
    assert "开发计划" in spec.plan_md
    # 转 AgentSpec 不报错的必要条件满足
    assert isinstance(spec.to_agent_specs(), list)


# ------------------------------------------------------------------ 引擎
def test_engine_develop_deterministic(tmp_path):
    _make_project(tmp_path)
    engine = CodeDevEngine(workspace=str(tmp_path))
    dev = engine.develop("实现登录功能并补测试", cwd=tmp_path, decompose=True, verify=True, max_subtasks=4)
    assert len(dev.context_pack) > 0
    assert dev.spec is not None and len(dev.spec.subtasks) >= 2
    assert dev.cost_factor > 0.0
    assert dev.skill  # 蒸馏出技能提示
    # 分解后窄上下文子代理, 真实成本远低于盲跑基线 → 成本闸门内
    assert dev.within_budget is True


def test_engine_cost_gate_falls_back(tmp_path):
    # 造一个多文件项目, 让分解产生大量子任务 → 估算成本超 20% 预算 → 回退单上下文直跑
    big = tmp_path / "bigpkg"
    big.mkdir()
    (big / "__init__.py").write_text("", encoding="utf-8")
    for i in range(14):
        (big / f"mod_{i:02d}.py").write_text(
            f'def handler_{i}(x):\n    """处理第 {i} 类请求。"""\n    return x + {i}\n',
            encoding="utf-8",
        )
    engine = CodeDevEngine(workspace=str(tmp_path), cost_budget_pct=20.0)
    dev = engine.develop("实现一套请求处理器", cwd=tmp_path, decompose=True, verify=False,
                         max_subtasks=20, top_k=20)
    # 子任务过多 → 估算成本倍数 > 1.2 → 触发回退
    assert dev.cost_factor > 1.2
    assert dev.within_budget is False
    assert dev.context_pack  # 回退后仍有检索上下文可用


def test_engine_doctor():
    engine = CodeDevEngine(workspace=".")
    d = engine.doctor()
    assert "arch_orchestration" in d and "available_detectors" in d


# ------------------------------------------------------------------ 插件 / 工具
def test_codedev_service_and_tools_registered():
    kernel = build_kernel()
    assert kernel.require("codedev") is not None
    reg = kernel.require("tool_registry")
    names = {t.name for t in reg.tools}
    for n in ("codedev_retrieve", "codedev_verify", "codedev_spec", "codedev_develop"):
        assert n in names, f"{n} 未注册"


def test_codedev_retrieve_tool_handler(tmp_path, qxt_home):
    _make_project(tmp_path)
    kernel = build_kernel()
    reg = kernel.require("tool_registry")
    tool = reg.get("codedev_retrieve")
    ctx = ToolContext(kernel=kernel, workspace=str(tmp_path), confirm=lambda _p: True)
    out = tool.handler(ctx=ctx, task="实现登录功能", top_k=5)
    assert "auth.py" in out and "login" in out
