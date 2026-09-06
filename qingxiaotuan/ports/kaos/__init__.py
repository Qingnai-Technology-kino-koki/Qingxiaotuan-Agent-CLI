"""自研实现 (对齐上游 ``kaos`` 的接口语义; 归属见 NOTICE) —— 纯逻辑、零依赖子集。

Re-exports the public API for environment path resolution, login-shell-path
computation, and local/current environment detection helpers. Backends that
shell out to OS-specific binaries (``LocalKaos`` / ``SSHKaos``) and node-only
convenience wrappers are intentionally omitted — see ``SKIPPED.md``.
"""

from __future__ import annotations

from .current import (
    chdir,
    exec,
    exec_with_env,
    get_current_kaos,
    getcwd,
    gethome,
    mkdir,
    normpath,
    path_class,
    read_bytes,
    read_text,
    run_with_kaos,
    set_current_kaos,
    stat,
    write_bytes,
    write_text,
)
from .environment import (
    Environment,
    EnvironmentDeps,
    GIT_EXEC_PATH_TIMEOUT_MS,
    MINGW_PREFIX_SET,
    OsKind,
    ShellName,
    dedupe_windows_paths,
    detect_environment,
    find_executables_on_path,
    git_bash_candidates_from_git_exec_path,
    git_bash_candidates_from_git_exe,
    git_bash_candidates_from_git_root,
    is_absolute_windows_path,
    locate_windows_git_bash,
    normalize_windows_path,
    read_git_exec_path,
    resolve_os_kind,
)
from .errors import (
    KaosError,
    KaosFileExistsError,
    KaosShellNotFoundError,
    KaosValueError,
)
from .internal import decode_text_with_errors, glob_pattern_to_regex
from .kaos import Kaos
from .login_shell_path import (
    LOGIN_SHELL_ENV_TIMEOUT_MS,
    LoginShellPathDeps,
    apply_login_shell_path,
    merge_login_shell_path,
    probe_login_shell_path,
)
from .shell_path_bridge import (
    ShellPathBridge,
    ShellPathBridgeDeps,
    ShellPathBridgeEnv,
    create_shell_path_bridge,
    translate_shell_drive_path,
)
from .types import KaosProcess, StatResult

__all__ = [
    # errors
    "KaosError",
    "KaosValueError",
    "KaosFileExistsError",
    "KaosShellNotFoundError",
    # types
    "StatResult",
    "KaosProcess",
    # environment
    "Environment",
    "EnvironmentDeps",
    "OsKind",
    "ShellName",
    "GIT_EXEC_PATH_TIMEOUT_MS",
    "MINGW_PREFIX_SET",
    "resolve_os_kind",
    "normalize_windows_path",
    "is_absolute_windows_path",
    "dedupe_windows_paths",
    "git_bash_candidates_from_git_root",
    "git_bash_candidates_from_git_exe",
    "git_bash_candidates_from_git_exec_path",
    "read_git_exec_path",
    "find_executables_on_path",
    "locate_windows_git_bash",
    "detect_environment",
    # login_shell_path
    "LoginShellPathDeps",
    "LOGIN_SHELL_ENV_TIMEOUT_MS",
    "probe_login_shell_path",
    "merge_login_shell_path",
    "apply_login_shell_path",
    # shell_path_bridge
    "ShellPathBridge",
    "ShellPathBridgeEnv",
    "ShellPathBridgeDeps",
    "translate_shell_drive_path",
    "create_shell_path_bridge",
    # internal
    "decode_text_with_errors",
    "glob_pattern_to_regex",
    # kaos protocol
    "Kaos",
    # current
    "get_current_kaos",
    "set_current_kaos",
    "run_with_kaos",
    "getcwd",
    "gethome",
    "normpath",
    "path_class",
    "stat",
    "mkdir",
    "chdir",
    "read_text",
    "write_text",
    "read_bytes",
    "write_bytes",
    "exec",
    "exec_with_env",
]
