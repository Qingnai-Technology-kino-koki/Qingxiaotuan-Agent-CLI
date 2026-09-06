"""Current-Kaos registry — bind a Kaos instance to the running async context.

自研实现 (对齐上游 current 语义)。 The original used node's ``AsyncLocalStorage``; the
idiomatic Python equivalent is :class:`contextvars.ContextVar`. This lets
library code reach "the Kaos for this context" without threading it through
every call, while concurrent tasks each keep their own binding.

Only the context machinery and the convenience wrappers are ported. The
convenience wrappers delegate to :func:`get_current_kaos`; they require a bound
Kaos (a concrete backend) to actually run, but the binding logic is the part
that is pure and testable.
"""

from __future__ import annotations

import contextvars
from typing import Callable, TypeVar

from .errors import KaosError
from .kaos import Kaos

T = TypeVar("T")

_kaos_var: contextvars.ContextVar[Kaos] = contextvars.ContextVar("kaos")


def get_current_kaos() -> Kaos:
    """Return the :class:`Kaos` bound to the current async context.

    Raises :class:`KaosError` if nothing is bound — callers must either call
    :func:`set_current_kaos` once at startup or wrap entry points in
    :func:`run_with_kaos`.
    """
    try:
        return _kaos_var.get()
    except LookupError:
        raise KaosError(
            "No Kaos is bound to the current context. Call "
            "`set_current_kaos(await LocalKaos.create())` once at startup, or "
            "wrap the call in `run_with_kaos(...)`."
        ) from None


def set_current_kaos(kaos: Kaos) -> None:
    """Bind ``kaos`` as the current instance for the running context tree."""
    _kaos_var.set(kaos)


def run_with_kaos(kaos: Kaos, fn: Callable[[], T]) -> T:
    """Run ``fn`` with ``kaos`` bound for its (async) subtree.

    Concurrent calls do not pollute each other — the binding is scoped to this
    invocation via a context token, mirroring ``AsyncLocalStorage.run``.
    """
    token = _kaos_var.set(kaos)
    try:
        return fn()
    finally:
        _kaos_var.reset(token)


# Module-level convenience functions delegating to the current Kaos instance.


def getcwd() -> str:
    return get_current_kaos().getcwd()


def gethome() -> str:
    return get_current_kaos().gethome()


def normpath(path: str) -> str:
    return get_current_kaos().normpath(path)


def path_class() -> "posix | win32":
    return get_current_kaos().path_class()


def stat(path: str, follow_symlinks: bool = True) -> "object":
    return get_current_kaos().stat(path, follow_symlinks)


def mkdir(path: str, parents: bool = False, exist_ok: bool = False) -> None:
    get_current_kaos().mkdir(path, parents, exist_ok)


def chdir(path: str) -> None:
    get_current_kaos().chdir(path)


def read_text(
    path: str, encoding: str = "utf-8", errors: "strict | replace | ignore" = "strict"
) -> str:
    return get_current_kaos().read_text(path, encoding, errors)


def write_text(
    path: str, data: str, mode: "w | a" = "w", encoding: str = "utf-8"
) -> int:
    return get_current_kaos().write_text(path, data, mode, encoding)


def read_bytes(path: str, n: int | None = None) -> bytes:
    return get_current_kaos().read_bytes(path, n)


def write_bytes(path: str, data: bytes) -> int:
    return get_current_kaos().write_bytes(path, data)


def exec(*args: str) -> object:
    return get_current_kaos().exec(*args)


def exec_with_env(args: list[str], env: dict[str, str] | None = None) -> object:
    return get_current_kaos().exec_with_env(args, env)


__all__ = [
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
