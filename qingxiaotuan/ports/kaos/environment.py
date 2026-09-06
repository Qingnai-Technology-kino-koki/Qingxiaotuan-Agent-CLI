"""Cross-platform OS / shell detection.

自研实现 (对齐上游 environment 探测语义)。 Detection is a pure function of injected probes
(``platform`` / ``arch`` / ``release`` / ``env`` / ``is_file`` /
``exec_file_text``) so the same suite runs identically on any host OS. The
production helper ``detect_environment_from_node`` shells out to the OS and
depends on node internals, so it is intentionally *not* ported — callers
inject a deps bag (see :func:`detect_environment`).

On Windows the probe expects bash from Git for Windows or MSYS2. If it cannot
be located the function raises :class:`KaosShellNotFoundError`; set
``KIMI_SHELL_PATH`` to override.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Awaitable, Callable, Literal

from .errors import KaosShellNotFoundError

OsKind = str
ShellName = Literal["bash", "sh"]

GIT_EXEC_PATH_TIMEOUT_MS = 5_000

MINGW_PREFIX_SET: frozenset[str] = frozenset(
    {"mingw32", "mingw64", "ucrt64", "clang64", "clangarm64"}
)

_POSIX_BASH_CANDIDATES: tuple[str, ...] = (
    "/bin/bash",
    "/usr/bin/bash",
    "/usr/local/bin/bash",
)


@dataclass(frozen=True, slots=True)
class Environment:
    """Resolved OS / shell probe describing a target environment."""

    os_kind: OsKind
    os_arch: str
    os_version: str
    shell_name: ShellName
    shell_path: str


@dataclass(slots=True)
class EnvironmentDeps:
    """Injected probes so detection stays pure and testable.

    ``is_file`` and ``exec_file_text`` are async to mirror the node async FS
    surface; tests supply in-memory stubs.
    """

    platform: str
    arch: str
    release: str
    env: dict[str, str | None]
    is_file: Callable[[str], Awaitable[bool]]
    exec_file_text: Callable[[str, tuple[str, ...], int], Awaitable[str | None]]


def resolve_os_kind(platform: str) -> OsKind:
    """Map a node-style ``process.platform`` string to a friendly OS name."""
    if platform == "darwin":
        return "macOS"
    if platform == "linux":
        return "Linux"
    if platform == "win32":
        return "Windows"
    return platform


def normalize_windows_path(path: str) -> str:
    """Replace forward slashes with backslashes (a lexical, not semantic, op)."""
    return path.replace("/", "\\")


def is_absolute_windows_path(path: str) -> bool:
    return PureWindowsPath(normalize_windows_path(path)).is_absolute()


def dedupe_windows_paths(paths: list[str]) -> list[str]:
    """Drop case-insensitive duplicate Windows paths, preserving first-seen order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for path in paths:
        key = normalize_windows_path(path).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def git_bash_candidates_from_git_root(root: str) -> tuple[str, str]:
    """Return ``(bin\\bash.exe, usr\\bin\\bash.exe)`` candidates under a git root."""
    root_p = PureWindowsPath(normalize_windows_path(root))
    return (
        os.path.normpath(str(root_p / "bin" / "bash.exe")),
        os.path.normpath(str(root_p / "usr" / "bin" / "bash.exe")),
    )


def git_bash_candidates_from_git_exe(git_exe: str) -> tuple[str, str] | None:
    """Infer bash paths from a ``git.exe`` location.

    Most Git for Windows installs put ``git.exe`` in ``<root>\\cmd\\git.exe`` or
    ``<root>\\bin\\git.exe`` with bash at ``<root>\\bin\\bash.exe``. Other
    layouts (package-manager shims) must resolve through ``git --exec-path``.
    """
    git_exe_p = PureWindowsPath(normalize_windows_path(git_exe))
    git_dir = git_exe_p.parent
    git_dir_name = git_dir.name.lower()
    if git_dir_name not in ("cmd", "bin"):
        return None
    return git_bash_candidates_from_git_root(str(git_dir.parent))


def git_bash_candidates_from_git_exec_path(exec_path: str) -> tuple[str, str]:
    """Infer bash paths from ``git --exec-path`` output (a mingw dir)."""
    normalized = normalize_windows_path(exec_path)
    parts = normalized.split("\\")
    for i in range(len(parts) - 1, -1, -1):
        segment = parts[i].lower()
        if segment in MINGW_PREFIX_SET:
            root = "\\".join(parts[:i])
            if root:
                return git_bash_candidates_from_git_root(root)
    # No mingw segment found: the exec path is typically ``<root>/libexec/git-core``
    # so the git root is two levels up.
    return git_bash_candidates_from_git_root(
        str(PureWindowsPath(normalized).joinpath("..", ".."))
    )


async def read_git_exec_path(deps: EnvironmentDeps, git_exe: str) -> str | None:
    """Run ``git --exec-path`` and return the first non-empty line, if any."""
    if deps.platform == "win32" and not is_absolute_windows_path(git_exe):
        return None
    stdout = await deps.exec_file_text(git_exe, ("--exec-path",), GIT_EXEC_PATH_TIMEOUT_MS)
    if stdout is None:
        return None
    for line in stdout.splitlines():
        exec_path = line.strip()
        if exec_path:
            return exec_path
    return None


async def find_executables_on_path(
    name: str,
    path_env: str | None,
    platform: str,
    is_file: Callable[[str], Awaitable[bool]],
) -> list[str]:
    """Return absolute paths to ``name`` found on the (already-split) ``PATH``."""
    if not path_env:
        return []
    list_sep = ";" if platform == "win32" else ":"
    dir_sep = "\\" if platform == "win32" else "/"
    found: list[str] = []
    for raw_dir in path_env.split(list_sep):
        directory = raw_dir.strip()
        if not directory:
            continue
        if platform == "win32" and not is_absolute_windows_path(directory):
            continue
        candidate = (
            f"{directory}{name}" if directory.endswith(dir_sep) else f"{directory}{dir_sep}{name}"
        )
        if await is_file(candidate):
            found.append(candidate)
    return dedupe_windows_paths(found) if platform == "win32" else found


async def locate_windows_git_bash(deps: EnvironmentDeps) -> str:
    """Probe the host for a Git Bash ``bash.exe``, raising if none is found."""
    checked: list[str] = []

    override = deps.env.get("KIMI_SHELL_PATH")
    if override is not None:
        override = override.strip()
        if override:
            checked.append(override)
            if await deps.is_file(override):
                return override

    git_executables = await find_executables_on_path(
        "git.exe", deps.env.get("PATH"), deps.platform, deps.is_file
    )

    for git_exe in git_executables:
        inferred = git_bash_candidates_from_git_exe(git_exe)
        if inferred is not None:
            for candidate in inferred:
                checked.append(candidate)
                if await deps.is_file(candidate):
                    return candidate

        git_exec_path = await read_git_exec_path(deps, git_exe)
        if git_exec_path is None:
            continue
        for candidate in git_bash_candidates_from_git_exec_path(git_exec_path):
            checked.append(candidate)
            if await deps.is_file(candidate):
                return candidate

    candidates = [
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
        "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
        "C:\\Program Files (x86)\\Git\\usr\\bin\\bash.exe",
    ]
    local_app_data = deps.env.get("LOCALAPPDATA")
    if local_app_data is not None:
        local_app_data = local_app_data.strip()
        if local_app_data:
            candidates.append(f"{local_app_data}\\Programs\\Git\\bin\\bash.exe")
            candidates.append(f"{local_app_data}\\Programs\\Git\\usr\\bin\\bash.exe")

    for candidate in candidates:
        checked.append(candidate)
        if await deps.is_file(candidate):
            return candidate

    raise KaosShellNotFoundError(
        "Git Bash was not found on this Windows host. Install Git for Windows "
        "from https://gitforwindows.org/ or set KIMI_SHELL_PATH to a bash.exe. "
        f"Checked: {', '.join(checked)}."
    )


async def detect_environment(deps: EnvironmentDeps) -> Environment:
    """Pure, async environment detection driven entirely by ``deps``."""
    os_kind = resolve_os_kind(deps.platform)
    os_arch = deps.arch
    os_version = deps.release

    if deps.platform == "win32":
        shell_path = await locate_windows_git_bash(deps)
        return Environment(os_kind, os_arch, os_version, "bash", shell_path)

    for candidate in _POSIX_BASH_CANDIDATES:
        if await deps.is_file(candidate):
            return Environment(os_kind, os_arch, os_version, "bash", candidate)
    return Environment(os_kind, os_arch, os_version, "sh", "/bin/sh")


__all__ = [
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
]
