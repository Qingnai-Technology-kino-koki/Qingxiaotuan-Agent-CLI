"""Behavioral tests for the ported ``qingxiaotuan.ports.kaos`` package.

Covers environment path resolution, login-shell-path computation, the
shell-path bridge, internal helpers, and the current-Kaos context machinery.
Async deps are stubbed in-memory; the suite runs with stdlib only.
"""

from __future__ import annotations

import asyncio
from pathlib import PureWindowsPath

import pytest

from qingxiaotuan.ports.kaos import (
    Environment,
    EnvironmentDeps,
    KaosError,
    KaosShellNotFoundError,
    LoginShellPathDeps,
    apply_login_shell_path,
    ShellPathBridge,
    ShellPathBridgeEnv,
    ShellPathBridgeDeps,
    StatResult,
    create_shell_path_bridge,
    decode_text_with_errors,
    dedupe_windows_paths,
    detect_environment,
    find_executables_on_path,
    git_bash_candidates_from_git_exe,
    git_bash_candidates_from_git_exec_path,
    git_bash_candidates_from_git_root,
    glob_pattern_to_regex,
    is_absolute_windows_path,
    locate_windows_git_bash,
    merge_login_shell_path,
    normalize_windows_path,
    probe_login_shell_path,
    resolve_os_kind,
    translate_shell_drive_path,
)
from qingxiaotuan.ports.kaos import current as current_mod


# ── helpers ────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def make_env_deps(platform, *, files=(), env=None, exec_map=None):
    """Build an ``EnvironmentDeps`` with in-memory ``is_file`` / ``exec_file_text``."""
    file_set = set(files)

    async def is_file(p):
        return p in file_set

    async def exec_file_text(file, args, timeout_ms):
        if exec_map is None:
            return None
        return exec_map.get((file, args))

    return EnvironmentDeps(
        platform=platform,
        arch="x64",
        release="1.0",
        env=dict(env or {}),
        is_file=is_file,
        exec_file_text=exec_file_text,
    )


# ── environment: resolve_os_kind / windows path helpers ─────────────────────

def test_resolve_os_kind():
    assert resolve_os_kind("darwin") == "macOS"
    assert resolve_os_kind("linux") == "Linux"
    assert resolve_os_kind("win32") == "Windows"
    assert resolve_os_kind("freebsd") == "freebsd"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("C:\\foo\\bar", True),
        ("C:/foo", True),
        ("\\\\server\\share", True),
        ("relative/path", False),
        ("/usr/bin", False),
    ],
)
def test_is_absolute_windows_path(path, expected):
    assert is_absolute_windows_path(path) is expected


def test_normalize_windows_path():
    assert normalize_windows_path("C:/foo/bar") == "C:\\foo\\bar"


def test_dedupe_windows_paths():
    out = dedupe_windows_paths(
        ["C:\\Git\\bin\\bash.exe", "c:\\git\\bin\\bash.exe", "D:\\x.exe"]
    )
    assert out == ["C:\\Git\\bin\\bash.exe", "D:\\x.exe"]


# ── environment: git bash candidate inference ───────────────────────────────

def test_git_bash_candidates_from_git_exe():
    assert git_bash_candidates_from_git_exe("C:\\Git\\cmd\\git.exe") == (
        "C:\\Git\\bin\\bash.exe",
        "C:\\Git\\usr\\bin\\bash.exe",
    )
    assert git_bash_candidates_from_git_exe("C:\\Git\\bin\\git.exe") == (
        "C:\\Git\\bin\\bash.exe",
        "C:\\Git\\usr\\bin\\bash.exe",
    )
    # shim in a non-cmd/bin dir: no inference
    assert git_bash_candidates_from_git_exe("C:\\scoop\\shims\\git.exe") is None


def test_git_bash_candidates_from_git_exec_path():
    # mingw64 segment anchors the git root
    assert git_bash_candidates_from_git_exec_path(
        "C:\\Git\\mingw64\\libexec\\git-core"
    ) == (
        "C:\\Git\\bin\\bash.exe",
        "C:\\Git\\usr\\bin\\bash.exe",
    )
    # no mingw segment → root is two levels up from libexec/git-core
    assert git_bash_candidates_from_git_exec_path(
        "C:\\Git\\libexec\\git-core"
    ) == (
        "C:\\Git\\bin\\bash.exe",
        "C:\\Git\\usr\\bin\\bash.exe",
    )


def test_git_bash_candidates_from_git_root():
    assert git_bash_candidates_from_git_root("C:\\Git") == (
        "C:\\Git\\bin\\bash.exe",
        "C:\\Git\\usr\\bin\\bash.exe",
    )


# ── environment: find_executables_on_path ──────────────────────────────────

def test_find_executables_on_path_posix():
    deps = make_env_deps("linux", files=["/usr/bin/git.exe"])
    out = run(
        find_executables_on_path("git.exe", "/usr/bin:/bin", "linux", deps.is_file)
    )
    assert out == ["/usr/bin/git.exe"]


def test_find_executables_on_path_win32_skips_relative_dirs():
    deps = make_env_deps(
        "win32",
        files=["C:\\Git\\cmd\\git.exe"],
    )
    out = run(
        find_executables_on_path(
            "git.exe", "relative\\dir;C:\\Git\\cmd", "win32", deps.is_file
        )
    )
    assert out == ["C:\\Git\\cmd\\git.exe"]


# ── environment: detect_environment ────────────────────────────────────────

def test_detect_environment_posix_bash_found():
    deps = make_env_deps("linux", files=["/bin/bash"])
    env = run(detect_environment(deps))
    assert env.os_kind == "Linux"
    assert env.shell_name == "bash"
    assert env.shell_path == "/bin/bash"


def test_detect_environment_posix_falls_back_to_sh():
    deps = make_env_deps("darwin", files=[])  # no bash candidates
    env = run(detect_environment(deps))
    assert env.os_kind == "macOS"
    assert env.shell_name == "sh"
    assert env.shell_path == "/bin/sh"


def test_detect_environment_win32_kimi_shell_path_override():
    deps = make_env_deps(
        "win32", files=["D:\\mybash\\bash.exe"], env={"KIMI_SHELL_PATH": "D:\\mybash\\bash.exe"}
    )
    env = run(detect_environment(deps))
    assert env.shell_name == "bash"
    assert env.shell_path == "D:\\mybash\\bash.exe"


def test_detect_environment_win32_via_path_and_hardcoded():
    deps = make_env_deps(
        "win32",
        files=[
            "C:\\Git\\cmd\\git.exe",
            "C:\\Git\\bin\\bash.exe",
            "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
        ],
        env={"PATH": "C:\\Git\\cmd"},
    )
    env = run(detect_environment(deps))
    assert env.shell_path == "C:\\Git\\bin\\bash.exe"


def test_detect_environment_win32_not_found_raises():
    deps = make_env_deps("win32", files=[], env={})
    with pytest.raises(KaosShellNotFoundError):
        run(detect_environment(deps))
    # and the explicit locator also raises
    with pytest.raises(KaosShellNotFoundError):
        run(locate_windows_git_bash(deps))


# ── login-shell-path: merge ────────────────────────────────────────────────

def test_merge_login_shell_path_appends_missing_absolute():
    out = merge_login_shell_path("/usr/bin:/bin", "/usr/bin:/opt/homebrew/bin:/foo")
    assert out == "/usr/bin:/bin:/opt/homebrew/bin:/foo"


def test_merge_login_shell_path_skips_relative_and_empty():
    out = merge_login_shell_path("/usr/bin", "/usr/bin:.:relative:/opt/x")
    assert out == "/usr/bin:/opt/x"


def test_merge_login_shell_path_none_current_keeps_additions_only():
    out = merge_login_shell_path(None, "/opt/homebrew/bin:/usr/bin")
    assert out == "/opt/homebrew/bin:/usr/bin"


def test_merge_login_shell_path_set_but_empty_preserves_leading_colon():
    out = merge_login_shell_path("", "/opt/x")
    assert out == ":/opt/x"


def test_merge_login_shell_path_no_change():
    assert merge_login_shell_path("/usr/bin:/opt/x", "/usr/bin") == "/usr/bin:/opt/x"
    assert merge_login_shell_path("/usr/bin", "/usr/bin") == "/usr/bin"


# ── login-shell-path: probe + apply ────────────────────────────────────────

def test_probe_login_shell_path_win32_none():
    deps = LoginShellPathDeps("win32", {}, lambda: "/bin/sh", _never)
    assert run(probe_login_shell_path(deps)) is None


def test_probe_login_shell_path_falls_back_to_user_shell():
    async def exec_stub(file, args, timeout):
        return "PATH=/zsh/bin:/usr/bin\n"

    deps = LoginShellPathDeps(
        "linux",
        {"SHELL": "   "},  # blank → fallback
        lambda: "/bin/zsh",
        exec_stub,
    )
    assert run(probe_login_shell_path(deps)) == "/zsh/bin:/usr/bin"


def test_probe_login_shell_path_takes_last_path_line():
    out = "/usr/bin\nPATH=/real/bin:/usr/bin\nprofile spam\nPATH=/should/win:/real/bin\n"

    async def exec_stub(file, args, timeout):
        return out

    deps = LoginShellPathDeps(
        "linux", {"SHELL": "/bin/bash"}, lambda: None, exec_stub
    )
    assert run(probe_login_shell_path(deps)) == "/should/win:/real/bin"


def test_probe_login_shell_path_no_path_none():
    async def exec_stub(file, args, timeout):
        return "no path here\n"

    deps = LoginShellPathDeps(
        "linux", {"SHELL": "/bin/bash"}, lambda: None, exec_stub
    )
    assert run(probe_login_shell_path(deps)) is None


def test_apply_login_shell_path_merges_into_env():
    env = {"PATH": "/usr/bin", "SHELL": "/bin/bash"}

    async def exec_stub(file, args, timeout):
        return "PATH=/usr/bin:/opt/new\n"

    deps = LoginShellPathDeps("linux", env, lambda: None, exec_stub)
    run(apply_login_shell_path(deps))
    assert env["PATH"] == "/usr/bin:/opt/new"


def test_apply_login_shell_path_noop_when_nothing_new():
    env = {"PATH": "/usr/bin:/opt/new", "SHELL": "/bin/bash"}

    async def exec_stub(file, args, timeout):
        return "PATH=/usr/bin:/opt/new\n"

    deps = LoginShellPathDeps("linux", env, lambda: None, exec_stub)
    run(apply_login_shell_path(deps))
    assert env["PATH"] == "/usr/bin:/opt/new"  # unchanged


async def _never(file, args, timeout):
    return None


# ── shell-path-bridge: lexical translation ──────────────────────────────────

def test_translate_shell_drive_path():
    assert translate_shell_drive_path("/c/Users/x") == "C:/Users/x"
    assert translate_shell_drive_path("/c:/Users/x") == "C:/Users/x"
    assert translate_shell_drive_path("/cygdrive/c/Users/x") == "C:/Users/x"
    assert translate_shell_drive_path("/usr/local/bin") == "/usr/local/bin"


def test_shell_path_bridge_to_shell_path():
    env = ShellPathBridgeEnv("Windows", "bash", "C:\\Git\\bin\\bash.exe")
    bridge = create_shell_path_bridge(env, ShellPathBridgeDeps(_cygpath_sync, lambda p: False))
    assert bridge.to_shell_path("C:\\Users\\x") == "/c/Users/x"
    assert bridge.to_shell_path("C:\\") == "/c/"
    assert bridge.to_shell_path("\\\\server\\share") == "//server/share"


def test_shell_path_bridge_to_shell_path_identity_off_windows():
    env = ShellPathBridgeEnv("Linux", "bash", "/bin/bash")
    bridge = create_shell_path_bridge(env, ShellPathBridgeDeps(_cygpath_sync, lambda p: False))
    assert bridge.to_shell_path("C:\\Users\\x") == "C:\\Users\\x"


def test_shell_path_bridge_from_shell_path_via_cygpath():
    env = ShellPathBridgeEnv("Windows", "bash", "C:\\Git\\bin\\bash.exe")
    deps = ShellPathBridgeDeps(
        lambda f, a: "C:\\foo" if a == ("-w", "-C", "UTF8", "--", "/foo") else "",
        lambda p: str(p).endswith("cygpath.exe"),  # cygpath located next to shell
    )
    bridge = create_shell_path_bridge(env, deps)
    assert bridge.from_shell_path("/foo/bar") == "C:/foo/bar"


def test_shell_path_bridge_from_shell_path_passthrough_without_cygpath():
    env = ShellPathBridgeEnv("Windows", "bash", "C:\\Git\\bin\\bash.exe")
    deps = ShellPathBridgeDeps(_cygpath_sync, lambda p: False)  # no cygpath found
    bridge = create_shell_path_bridge(env, deps)
    assert bridge.from_shell_path("/foo/bar") == "/foo/bar"  # lexical only, no drive


def test_shell_path_bridge_from_shell_path_virtual_fs():
    env = ShellPathBridgeEnv("Windows", "bash", "C:\\Git\\bin\\bash.exe")
    bridge = create_shell_path_bridge(env, ShellPathBridgeDeps(_cygpath_sync, lambda p: False))
    assert bridge.from_shell_path("/dev/null") == "/dev/null"
    assert bridge.from_shell_path("/proc/1") == "/proc/1"


def test_shell_path_bridge_from_shell_path_identity_off_windows():
    env = ShellPathBridgeEnv("Linux", "bash", "/bin/bash")
    bridge = create_shell_path_bridge(env, ShellPathBridgeDeps(_cygpath_sync, lambda p: False))
    assert bridge.from_shell_path("/c/Users/x") == "/c/Users/x"


def _cygpath_sync(file, args):
    return ""


# ── internal: decode + glob ─────────────────────────────────────────────────

def test_decode_text_strict_raises():
    with pytest.raises(UnicodeDecodeError):
        decode_text_with_errors(b"\xff\xfe", "utf-8", "strict")


def test_decode_text_replace():
    assert decode_text_with_errors(b"a\xffb", "utf-8", "replace") == "a\ufffdb"


def test_decode_text_ignore_preserves_valid_replacement():
    # U+FFFD (ef bf bd) is valid; a trailing invalid byte is dropped.
    data = "ok→".encode("utf-8") + b"\xff"
    out = decode_text_with_errors(data, "utf-8", "ignore")
    assert "→" in out
    assert "\ufffd" not in out


def test_decode_text_lossless_non_utf():
    assert decode_text_with_errors(b"\xff\x00", "latin-1") == "ÿ\x00"


def test_decode_text_bom_handling():
    payload = "hi".encode("utf-8-sig")
    # ignore_bom=True preserves the BOM as U+FEFF (TextDecoder ignoreBOM:true)
    assert decode_text_with_errors(payload, "utf-8", "strict", ignore_bom=True) == "\ufeffhi"
    # default strips the BOM marker (TextDecoder default ignoreBOM:false)
    assert decode_text_with_errors(payload, "utf-8", "strict") == "hi"


def test_glob_pattern_to_regex():
    rx = glob_pattern_to_regex("*.txt")
    assert rx.match("foo.txt") and not rx.match("foo/bar.txt")

    rx = glob_pattern_to_regex("file?.log")
    assert rx.match("file1.log") and not rx.match("file12.log")

    rx = glob_pattern_to_regex("*.py", case_sensitive=False)
    assert rx.match("X.PY")

    rx = glob_pattern_to_regex("[!a-c]*.txt")
    assert rx.match("d.txt") and not rx.match("a.txt")

    # literal dot is escaped
    assert glob_pattern_to_regex("a.b").match("a.b")
    assert not glob_pattern_to_regex("a.b").match("axb")


# ── current: context machinery ─────────────────────────────────────────────

class _StubKaos:
    name = "stub"

    def getcwd(self):
        return "/stub/cwd"

    def gethome(self):
        return "/stub/home"


def test_current_kaos_unbound_raises():
    with pytest.raises(KaosError):
        current_mod.get_current_kaos()


def test_current_kaos_set_and_get():
    kaos = _StubKaos()
    current_mod.set_current_kaos(kaos)
    assert current_mod.get_current_kaos() is kaos
    assert current_mod.getcwd() == "/stub/cwd"
    assert current_mod.gethome() == "/stub/home"


def test_run_with_kaos_isolates_context():
    outer = _StubKaos()
    inner = _StubKaos()
    inner.name = "inner"
    current_mod.set_current_kaos(outer)

    captured = {}

    def fn():
        current_mod.set_current_kaos(inner)
        captured["inside"] = current_mod.get_current_kaos()
        return 42

    assert current_mod.run_with_kaos(inner, fn) == 42
    # outer binding restored after run_with_kaos returns
    assert current_mod.get_current_kaos() is outer


# ── types ──────────────────────────────────────────────────────────────────

def test_stat_result_dataclass():
    s = StatResult(0, 1, 2, 1, 1000, 1000, 16, 1.0, 2.0, 3.0)
    assert s.st_size == 16
    assert s.st_uid == 1000
    # frozen
    with pytest.raises(Exception):
        s.st_size = 99  # type: ignore[misc]
