"""Shell path bridge — translate between native win32 and MSYS2/Git Bash paths.

自研实现 (对齐上游 shell-path-bridge 语义)。 The msys runtime gives the shell a POSIX path
view native Python cannot resolve (``/c/Users/x`` is ``C:\\Users\\x``;
``/tmp/x`` is ``%TEMP%\\x``). ``to_shell_path`` renders native paths for bash
command lines; ``from_shell_path`` resolves model/shell-supplied paths for fs
access, translating drive-letter forms lexically and other root-relative paths
through an injected ``cygpath`` resolver. Anything unconvertible passes through
unchanged, and both directions are identity outside Windows bash.

``create_shell_path_bridge`` takes injectable deps for tests; the production
``get_shell_path_bridge`` (which shells out to ``execFileSync``/``existsSync``)
is not ported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Callable

DRIVE_COLON_RE = re.compile(r"^/([a-zA-Z]):(?:[\\/]|$)")
CYGDRIVE_RE = re.compile(r"^/cygdrive/([a-zA-Z])(?:/|$)")
DRIVE_RE = re.compile(r"^/([a-zA-Z])(?:/|$)")
WIN32_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# cygpath semantics are undefined for the virtual filesystems.
VIRTUAL_FS_PREFIXES: tuple[str, ...] = ("/dev/", "/proc/", "/sys/")


def join_drive(letter: str, rest: str) -> str:
    """Build a native win32 path from a drive letter and a (POSIX) rest segment."""
    normalized_rest = rest.replace("\\", "/")
    if normalized_rest == "":
        return f"{letter.upper()}:/"
    return f"{letter.upper()}:{normalized_rest}"


def translate_shell_drive_path(path: str) -> str:
    """Lexically translate shell-dialect drive paths to native win32 form.

    Pure string rewriting, no cygpath involved. Handles ``/c/x``, ``/c:/x`` and
    ``/cygdrive/c/x``. Anything else is returned unchanged.
    """
    colon_match = DRIVE_COLON_RE.match(path)
    if colon_match is not None:
        return join_drive(colon_match.group(1), path[3:])
    cygdrive_match = CYGDRIVE_RE.match(path)
    if cygdrive_match is not None:
        letter = cygdrive_match.group(1)
        return join_drive(letter, path[len(f"/cygdrive/{letter}") :])
    drive_match = DRIVE_RE.match(path)
    if drive_match is not None:
        return join_drive(drive_match.group(1), path[2:])
    return path


@dataclass(frozen=True, slots=True)
class ShellPathBridgeEnv:
    """The subset of :class:`~qingxiaotuan.ports.kaos.environment.Environment` the bridge needs."""

    os_kind: str
    shell_name: str
    shell_path: str


@dataclass(slots=True)
class ShellPathBridgeDeps:
    """Injectable OS hooks so the bridge is testable without node."""

    exec_file_sync: Callable[[str, tuple[str, ...]], str]
    is_file: Callable[[str], bool]


class ShellPathBridge:
    """Translate between native win32 and shell-dialect paths.

    Synchronous and self-contained. ``cygpath.exe`` is located lazily on first
    use; a missing one degrades permanently to lexical/pass-through behavior.
    """

    def __init__(self, env: ShellPathBridgeEnv, deps: ShellPathBridgeDeps) -> None:
        self._enabled = env.os_kind == "Windows" and env.shell_name == "bash"
        self._env = env
        self._deps = deps
        self._cygpath_exe: str | None | None = None  # None-unsearched / str / literal-None
        self._cygpath_searched = False
        self._segment_cache: dict[str, str] = {}

    def _locate_cygpath(self) -> str | None:
        if self._cygpath_searched:
            return self._cygpath_exe
        shell_dir = str(PureWindowsPath(self._env.shell_path).parent)
        shell_dir_p = PureWindowsPath(shell_dir)
        candidates = [str(shell_dir_p / "cygpath.exe")]
        if shell_dir_p.name.lower() == "bin":
            candidates.append(str(shell_dir_p / ".." / "usr" / "bin" / "cygpath.exe"))
        found = next((c for c in candidates if self._deps.is_file(c)), None)
        self._cygpath_exe = found
        self._cygpath_searched = True
        return found

    def _resolve_root_segment(self, first_segment: str) -> str | None:
        cached = self._segment_cache.get(first_segment)
        if cached is not None or first_segment in self._segment_cache:
            return self._segment_cache.get(first_segment)
        exe = self._locate_cygpath()
        if exe is None:
            self._segment_cache[first_segment] = None  # type: ignore[assignment]
            return None
        try:
            output = self._deps.exec_file_sync(
                exe, ("-w", "-C", "UTF8", "--", f"/{first_segment}")
            )
        except Exception:
            return None
        # cygpath appends a newline and may emit a trailing separator (``D:\\``).
        trimmed = re.sub(r"\r?\n$", "", output)
        if not WIN32_DRIVE_ABSOLUTE_RE.match(trimmed) and not trimmed.startswith("\\\\"):
            return None
        resolved = re.sub(r"[\\/]$", "", trimmed)
        self._segment_cache[first_segment] = resolved
        return resolved

    def from_shell_path(self, path: str) -> str:
        """Model/shell-supplied path → native, for fs access. Identity when not convertible."""
        if not self._enabled:
            return path
        # Keep UNC out first: posix.normalize would collapse the leading ``//``.
        if path.startswith("//"):
            return path
        if path.startswith("/"):
            normalized = str(PurePosixPath(path))  # POSIX normalize (collapses . and ..)
            lexical = translate_shell_drive_path(normalized)
            if lexical != normalized:
                return lexical
            if normalized == "/":
                return normalized
            if any(normalized.startswith(prefix) for prefix in VIRTUAL_FS_PREFIXES):
                return normalized
            first_segment = normalized[1:].split("/")[0]
            prefix = self._resolve_root_segment(first_segment)
            if prefix is None:
                return normalized
            remainder = normalized[1 + len(first_segment) :]
            joined = (f"{prefix}{remainder}").replace("\\", "/")
            # A mounted drive root resolved from a bare segment (``D:``) stays absolute.
            if re.match(r"^[A-Za-z]:$", joined):
                return f"{joined}/"
            return joined
        return path

    def to_shell_path(self, native_path: str) -> str:
        """Native win32 path → shell dialect, for building bash commands. Identity on posix."""
        if not self._enabled:
            return native_path
        if native_path.startswith("\\\\"):
            return native_path.replace("\\", "/")
        drive_match = re.match(r"^([A-Za-z]):(?:[\\/]|$)", native_path)
        if drive_match is not None:
            drive = drive_match.group(1).lower()
            rest = native_path[2:].replace("\\", "/")
            return f"/{drive}{rest if rest.startswith('/') else f'/{rest}'}"
        return native_path.replace("\\", "/")


def create_shell_path_bridge(env: ShellPathBridgeEnv, deps: ShellPathBridgeDeps) -> ShellPathBridge:
    """Build a bridge from an env snapshot and injectable OS hooks."""
    return ShellPathBridge(env, deps)


__all__ = [
    "ShellPathBridge",
    "ShellPathBridgeEnv",
    "ShellPathBridgeDeps",
    "translate_shell_drive_path",
    "create_shell_path_bridge",
]
