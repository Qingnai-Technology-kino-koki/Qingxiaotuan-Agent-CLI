"""qxt gh 原生绑定测试 —— 安全闸门拦截 + 受保护放行 + 退出码透传。"""

from __future__ import annotations

import io
import sys

from qingxiaotuan.gh.native import (
    NativeGH, GHNotInstalledError, EXIT_FORBIDDEN, classify,
)
from qingxiaotuan.gh.policy import SafetyPolicy


class RecordingRunner:
    """记录是否被派生, 返回可控 (rc, out, err)。"""

    def __init__(self, rc=0, out="", err=""):
        self.calls: list = []
        self.rc, self.out, self.err = rc, out, err

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        return self.rc, self.out, self.err


def test_forbidden_never_spawns_subprocess():
    runner = RecordingRunner(out="SHOULD NOT HAPPEN")
    gh = NativeGH(runner=runner, gh_path="fake-gh")
    for cmd in (["repo", "delete", "user/repo"],
                ["api", "-X", "DELETE", "repos/user/repo"]):
        rc, out, err = gh.run(cmd)
        assert rc == EXIT_FORBIDDEN
        assert "安全拦截" in err
        assert runner.calls == []  # 破坏性操作 → 永不派生进程


def test_forbidden_cannot_be_bypassed_by_force_flags():
    gh = NativeGH(runner=RecordingRunner(), gh_path="fake-gh")
    for shell in (["repo", "delete", "u/r", "--yes", "--force"],
                  ["gist", "delete", "x", "-f", "-D", "-y"]):
        rc, _, err = gh.run(shell)
        assert rc == EXIT_FORBIDDEN
        assert "安全拦截" in err


def test_safe_passthrough_returns_gh_rc():
    runner = RecordingRunner(rc=42, out="line1\nline2")
    gh = NativeGH(runner=runner, gh_path="fake-gh")
    rc, out, err = gh.run(["repo", "view", "u/r"])
    assert runner.calls == [["fake-gh", "repo", "view", "u/r"]]
    assert rc == 42
    assert out == "line1\nline2"


def test_guarded_passthrough_with_notice(capsys):
    pol = SafetyPolicy(verbose_guarded=True)
    runner = RecordingRunner(rc=0, out="ok")
    gh = NativeGH(runner=runner, gh_path="fake-gh", policy=pol)
    rc, out, _ = gh.run(["issue", "close", "12"], _confirmed=True)
    assert runner.calls == [["fake-gh", "issue", "close", "12"]]
    assert "受保护操作" in capsys.readouterr().err
    assert out == "ok" and rc == 0


def test_guarded_requires_confirm_noninteractive():
    gh = NativeGH(runner=RecordingRunner(), gh_path="fake-gh", interactive=False)
    rc, out, _ = gh.run(["pr", "merge", "12"])  # 非交互、无 --confirm
    assert rc == 2
    assert "已取消" in out
    assert gh._runner.calls == []  # 未获确认 → 不派生


def test_guarded_confirm_flag_allows_passthrough():
    from qingxiaotuan.gh.native import EXIT_NEED_CONFIRM
    runner = RecordingRunner(rc=0, out="merged")
    gh = NativeGH(runner=runner, gh_path="fake-gh", interactive=False)
    rc, out, _ = gh.run(["pr", "merge", "12", "--confirm"])
    assert rc == 0
    # --confirm 被剥离, gh 只收到干净参数
    assert runner.calls == [["fake-gh", "pr", "merge", "12"]]
    assert out == "merged"


def test_guarded_interactive_accepts_y():
    runner = RecordingRunner(rc=0, out="closed")
    gh = NativeGH(runner=runner, gh_path="fake-gh", interactive=True,
                  prompter=lambda p: "y")
    rc, out, _ = gh.run(["issue", "close", "12"])
    assert rc == 0
    assert runner.calls == [["fake-gh", "issue", "close", "12"]]


def test_guarded_interactive_rejects_n():
    ran = []

    class R(RecordingRunner):
        def __call__(self, argv, **kw):
            ran.append(argv)
            return super().__call__(argv, **kw)

    gh = NativeGH(runner=R(), gh_path="fake-gh", interactive=True,
                  prompter=lambda p: "n")
    rc, _, _ = gh.run(["issue", "close", "12"])
    assert rc == 2
    assert ran == []  # 拒绝 → 未派生


def test_native_version():
    gh = NativeGH(runner=RecordingRunner(out="gh version 2.99\n"), gh_path="fake-gh")
    assert gh.version().startswith("gh version")
    assert gh.has_gh() is True


def test_missing_gh_raises_install_hint():
    gh = NativeGH(gh_path="")  # "" = 显式无 gh
    assert gh.has_gh() is False
    try:
        gh.run(["repo", "view", "u/r"])
        assert False, "应抛 GHNotInstalledError"
    except GHNotInstalledError as exc:
        assert "gh" in str(exc) and "安装" in str(exc)


# ---- fx5: 审计事件 (拦截 / 取消 / 受保护放行 均触发) ----

def test_audit_sink_events_for_blocked_cancelled_passed():
    """审计 sink 应在 FORBIDDEN / GUARDED取消 / GUARDED放行 三路径各记一次。"""
    events = []

    def sink(argv, decision, event, detail):
        events.append((list(argv), decision, event))

    # blocked
    gh = NativeGH(runner=RecordingRunner(), gh_path="fake-gh", audit_sink=sink)
    gh.run(["repo", "delete", "u/r"])
    # cancelled
    gh2 = NativeGH(runner=RecordingRunner(), gh_path="fake-gh",
                   interactive=False, audit_sink=sink)
    gh2.run(["pr", "merge", "5"])
    # guarded passed
    gh3 = NativeGH(runner=RecordingRunner(rc=0, out="ok"), gh_path="fake-gh",
                   interactive=False, audit_sink=sink)
    gh3.run(["issue", "close", "8", "--confirm"])

    kinds = [(d, e) for _, d, e in events]
    assert ("forbidden", "blocked") in kinds
    assert ("guarded", "cancelled") in kinds
    assert ("guarded", "guarded_passed") in kinds
    # 三个事件逐层对齐
    assert events[0] == (["repo", "delete", "u/r"], "forbidden", "blocked")
    assert events[1][2] == "cancelled"
    assert events[2][2] == "guarded_passed"


def test_audit_sink_default_writes_to_disk(tmp_path, monkeypatch):
    """默认 sink 把拦截记录写到 ~/.qingxiaotuan/logs/gh-audit.log。"""
    import qingxiaotuan.config.loader as _loader
    monkeypatch.setattr(_loader, "home_dir", lambda: tmp_path)
    from qingxiaotuan.gh.native import audit_sink_default

    audit_sink_default(["repo", "delete", "u/r"], "forbidden", "blocked", "x")
    log = tmp_path / "logs" / "gh-audit.log"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "repo" in text and "forbidden" in text and "blocked" in text


# ---- fx3: 认证引导 ----

def test_auth_failure_detector():
    from qingxiaotuan.cli.cmd_gh import _looks_like_auth_failure
    assert _looks_like_auth_failure("HTTP 401: Unauthorized")
    assert _looks_like_auth_failure("Please run 'gh auth login' to authenticate")
    assert _looks_like_auth_failure("not logged in")
    assert _looks_like_auth_failure("") is False
    assert _looks_like_auth_failure("repo not found") is False


def test_rest_fallback_flag_shown_in_no_gh():
    gh = NativeGH(gh_path="")
    assert gh.version() == "gh 未安装"