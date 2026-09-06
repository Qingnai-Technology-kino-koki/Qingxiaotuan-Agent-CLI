"""Login-shell PATH probe — enrich ``PATH`` with entries from the user's login shell.

自研实现 (对齐上游 login-shell-path 语义)。 When the agent is launched from a context that
skipped the user's shell profile (GUI launchers, non-login parent shells),
``PATH`` misses entries like ``/opt/homebrew/bin``. We run the user's login
shell once (``$SHELL -l -c /usr/bin/env``), extract its PATH, and append the
entries the current PATH lacks. Windows is skipped because the problem is
specific to POSIX login-shell profiles.

Like :mod:`environment`, the probe is a pure function of injected deps. The
production helpers that read ``process.env`` / call ``os.userInfo`` are not
ported (they depend on node internals).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

LOGIN_SHELL_ENV_TIMEOUT_MS = 5_000


@dataclass(slots=True)
class LoginShellPathDeps:
    """Injected probes so the login-shell probe stays pure and testable."""

    platform: str
    env: dict[str, str | None]
    user_shell: Callable[[], str | None]
    exec_file_text: Callable[[str, tuple[str, ...], int], Awaitable[str | None]]


async def probe_login_shell_path(deps: LoginShellPathDeps) -> str | None:
    """Run the user's login shell and return its PATH, or ``None``.

    Returns ``None`` when the probe does not apply (Windows, no resolvable
    shell) or fails (spawn error, timeout, no PATH in the output).
    """
    if deps.platform == "win32":
        return None

    # A set-but-blank $SHELL (some daemon/launchd envs) must also fall back.
    env_shell = deps.env.get("SHELL")
    if env_shell is not None:
        env_shell = env_shell.strip()
    shell = env_shell if env_shell else deps.user_shell()
    if not shell:
        return None

    stdout = await deps.exec_file_text(
        shell, ("-l", "-c", "/usr/bin/env"), LOGIN_SHELL_ENV_TIMEOUT_MS
    )
    if stdout is None:
        return None

    # Profile output lands on stdout before ``env`` runs, so keep the last
    # ``PATH=`` line.
    path: str | None = None
    for line in stdout.split("\n"):
        if line.startswith("PATH="):
            path = line[len("PATH=") :].strip()
    if not path:
        return None
    return path


def merge_login_shell_path(current_path: str | None, login_shell_path: str) -> str:
    """Union the current PATH with the login-shell PATH.

    The current PATH string is kept verbatim — including empty components,
    which POSIX command lookup treats as the current directory — and
    login-shell entries the current PATH lacks are appended in their own
    order. Only absolute login-shell entries are imported (``/``-leading on
    POSIX); empty, ``.`` and relative components are all cwd-dependent and are
    skipped. When nothing is missing the current string is returned unchanged.
    """
    current = current_path or ""
    seen: set[str] = {entry for entry in current.split(":") if entry}
    additions: list[str] = []
    for entry in login_shell_path.split(":"):
        if not entry.startswith("/") or entry in seen:
            continue
        seen.add(entry)
        additions.append(entry)
    if not additions:
        return current
    # ``None`` means "no PATH at all", so additions stand alone; ``''`` is a
    # real (cwd-only) PATH whose empty component must survive as a leading colon.
    if current_path is None:
        return ":".join(additions)
    return f"{current}:{':'.join(additions)}"


async def apply_login_shell_path(deps: LoginShellPathDeps) -> None:
    """Probe the login shell and merge its PATH into ``deps.env['PATH']``.

    Only writes when something was appended — an unset PATH stays unset (so the
    OS default search path is preserved) and a set PATH is not rewritten.
    """
    login_shell_path = await probe_login_shell_path(deps)
    if login_shell_path is None:
        return
    current_path = deps.env.get("PATH")
    merged = merge_login_shell_path(current_path, login_shell_path)
    if merged == (current_path or ""):
        return
    deps.env["PATH"] = merged


__all__ = [
    "LoginShellPathDeps",
    "LOGIN_SHELL_ENV_TIMEOUT_MS",
    "probe_login_shell_path",
    "merge_login_shell_path",
    "apply_login_shell_path",
]
