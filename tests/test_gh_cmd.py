"""qxt gh 命令路由测试 —— 假 gh 放 PATH, 端到端验证原生透传与拦截不派生。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


@pytest.fixture
def fake_gh_env(tmp_path, monkeypatch):
    """造一个 `gh` 假命令到 PATH: 记录被调用; 遇到 repo delete 写破坏标记。"""
    import qingxiaotuan.config.loader as _loader

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "gh.cmd"
    marker = tmp_path / "marker.txt"
    fake.write_text(
        "@echo off\n"
        "setlocal enabledelayedexpansion\n"
        "set ARGS=%*\n"
        "echo CMDLINE: %ARGS%\n"
        "echo %ARGS% | findstr /I /C:\"delete repo\" >nul && echo DELETED > \"" +
        str(marker).replace("\\", "\\\\") + "\"\n"
        "exit /b 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))

    import qingxiaotuan.cli.cmd_gh as _gh
    monkeypatch.setattr(_loader, "home_dir", lambda: tmp_path)
    return _gh, marker


def _args(gh_args, rest=False):
    return SimpleNamespace(gh_args=list(gh_args), rest=rest)


def test_native_passthrough_through_cmd(fake_gh_env, capsys):
    gh_cmd, _ = fake_gh_env
    # 安全命令 → 透传给假 gh, 原样拿到 CMDLINE。
    rc = gh_cmd.cmd_gh(_args(["repo", "view", "user/repo"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "CMDLINE: repo view user/repo" in out


def test_forbidden_never_spawns_through_cmd(fake_gh_env, capsys):
    gh_cmd, marker = fake_gh_env
    # 破坏性删除 → 脚本层拦截, 假 gh 不应被派生 (marker 不存在)。
    rc = gh_cmd.cmd_gh(_args(["repo", "delete", "user/repo", "--yes"]))
    err = capsys.readouterr().err
    assert rc == 3
    assert "安全拦截" in err
    assert not marker.exists(), "破坏性操作不得派生子进程"


def test_gh_cmd_requires_args_shows_usage(fake_gh_env, capsys):
    gh_cmd, _ = fake_gh_env
    rc = gh_cmd.cmd_gh(_args([]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "用法: qxt gh" in out


def test_policy_subcommand(fake_gh_env, capsys):
    gh_cmd, _ = fake_gh_env
    rc = gh_cmd.cmd_gh(_args(["policy", "show"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "GitHub 安全策略" in out


def test_borrow_retained_feature(fake_gh_env, capsys, monkeypatch):
    gh_cmd, _ = fake_gh_env
    import qingxiaotuan.tools.gh_borrow as _b
    monkeypatch.setattr(_b, "borrow",
                        lambda q, mode="code", limit=5: {"hits": [{"type": "fake"}]})
    monkeypatch.setattr(_b, "format_borrow",
                        lambda res: "【借鉴】" + str(res["hits"][0]))
    rc = gh_cmd.cmd_gh(_args(["borrow", "实现一个轮询器"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "借鉴" in out
    # 兼容新式 --json 透出
    rc2 = gh_cmd.cmd_gh(_args(["borrow", "x", "--json"]))
    out2 = capsys.readouterr().out
    assert '"hits"' in out2


def test_guarded_notice_on_stderr(fake_gh_env, capsys):
    gh_cmd, _ = fake_gh_env
    # 非交互 + --confirm → 打印影响提示后放行 (假 gh 已执行)
    rc = gh_cmd.cmd_gh(_args(["issue", "close", "12", "--confirm"]))
    err = capsys.readouterr().err
    assert rc == 0
    assert "受保护操作" in err


def test_guarded_blocked_without_confirm_noninteractive(fake_gh_env, capsys):
    gh_cmd, marker = fake_gh_env
    # 非交互且无 --confirm → 受保护操作要求确认, 退出码 2, 不派生
    rc = gh_cmd.cmd_gh(_args(["issue", "close", "12"]))
    out = capsys.readouterr().out
    assert rc == 2
    assert "已取消" in out
    assert not marker.exists()


def test_doctor_reports_no_gh(monkeypatch, capsys):
    """qxt gh doctor 在无 gh 时提示安装指引并返回 EXIT_NO_GH。"""
    fake_bin = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", "")
    import qingxiaotuan.cli.cmd_gh as gh_cmd
    from qingxiaotuan.gh.native import EXIT_NO_GH
    rc = gh_cmd.cmd_gh(_args(["doctor"]))
    out = capsys.readouterr().out
    assert rc == EXIT_NO_GH
    assert "gh CLI 未安装" in out and "安装" in out


def test_native_auth_failure_appends_refresh_guidance(fake_gh_env, monkeypatch, capsys):
    """gh 返回认证失败时, 透传原生报错并附刷新引导。"""
    gh_cmd, _ = fake_gh_env
    import qingxiaotuan.gh.native as _native

    def auth_fail_runner(argv, timeout=None, env=None):
        return 1, "", "HTTP 401: Unauthorized\nPlease run 'gh auth login'"

    monkeypatch.setattr(_native, "_runner_default", auth_fail_runner)
    rc = gh_cmd._run_native(["repo", "view", "u/r"], None)
    err = capsys.readouterr().err
    assert rc == 1
    assert "401" in err
    assert "auth login" in err or "刷新方式" in err


def test_audit_empty_reports_no_records(fake_gh_env, capsys):
    gh_cmd, _ = fake_gh_env
    rc = gh_cmd.cmd_gh(_args(["audit"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "尚无审计记录" in out


def test_audit_renders_records(fake_gh_env, capsys, tmp_path):
    gh_cmd, _ = fake_gh_env
    from qingxiaotuan.gh.audit import audit_log_path, append_event
    # 造两条审计记录
    append_event(["repo", "delete", "u/r"], "forbidden", "blocked", "x", home=tmp_path)
    append_event(["issue", "close", "5"], "guarded", "cancelled", "y", home=tmp_path)
    # 让 audit_log_path 指向 tmp 下 (与 home 一致)
    import qingxiaotuan.config.loader as _loader
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_loader, "home_dir", lambda: tmp_path)
    try:
        rc = gh_cmd.cmd_gh(_args(["audit"]))
        out = capsys.readouterr().out
        assert rc == 0
        assert "blocked" in out and "forbidden" in out
        assert "cancelled" in out and "guarded" in out
        # 限制条数
        rc2 = gh_cmd.cmd_gh(_args(["audit", "1"]))
        out2 = capsys.readouterr().out
        assert rc2 == 0
        assert "cancelled" in out2
        assert "forbidden" not in out2
    finally:
        monkeypatch.undo()


def test_audit_clear_removes_records(fake_gh_env, capsys, tmp_path):
    gh_cmd, _ = fake_gh_env
    from qingxiaotuan.gh.audit import audit_log_path, append_event
    append_event(["repo", "delete", "u/r"], "forbidden", "blocked", "x", home=tmp_path)
    import qingxiaotuan.config.loader as _loader
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_loader, "home_dir", lambda: tmp_path)
    try:
        rc = gh_cmd.cmd_gh(_args(["audit", "--clear"]))
        out = capsys.readouterr().out
        assert rc == 0
        assert "已清空" in out
        assert not audit_log_path(tmp_path).exists()
    finally:
        monkeypatch.undo()