"""The KAOS interface — a unified abstraction over execution environments.

自研实现 (对齐上游 kaos 接口语义)。 In Python this is a :class:`typing.Protocol` so local,
SSH and container backends can satisfy it structurally without a shared base
class. The protocol is intentionally minimal; concrete backends supply the
actual fs/exec behavior.
"""

from __future__ import annotations

from typing import AsyncGenerator, Protocol, runtime_checkable

from .types import StatResult


@runtime_checkable
class Kaos(Protocol):
    """Agent Operating System interface (upstream reference: see NOTICE).

    A unified API the agent uses to interact with different execution
    environments (local, SSH, containers, ...) through one surface.
    """

    name: str
    os_env: object  # Environment; typed loosely to avoid an import cycle

    def path_class(self) -> "posix | win32": ...

    def normpath(self, path: str) -> str: ...

    def gethome(self) -> str: ...

    def getcwd(self) -> str: ...

    def chdir(self, path: str) -> None: ...

    def with_cwd(self, cwd: str) -> "Kaos": ...

    def with_env(self, env: dict[str, str]) -> "Kaos": ...

    def stat(self, path: str, follow_symlinks: bool = True) -> StatResult: ...

    def iterdir(self, path: str) -> AsyncGenerator[str, None]: ...

    def glob(
        self, path: str, pattern: str, case_sensitive: bool = True
    ) -> AsyncGenerator[str, None]: ...

    def read_bytes(self, path: str, n: int | None = None) -> bytes: ...

    def read_text(
        self,
        path: str,
        encoding: str = "utf-8",
        errors: "strict | replace | ignore" = "strict",
    ) -> str: ...

    def read_lines(
        self,
        path: str,
        encoding: str = "utf-8",
        errors: "strict | replace | ignore" = "strict",
    ) -> AsyncGenerator[str, None]: ...

    def write_bytes(self, path: str, data: bytes) -> int: ...

    def write_text(
        self,
        path: str,
        data: str,
        mode: "w | a" = "w",
        encoding: str = "utf-8",
    ) -> int: ...

    def mkdir(self, path: str, parents: bool = False, exist_ok: bool = False) -> None: ...

    def exec(self, *args: str) -> object: ...

    def exec_with_env(self, args: list[str], env: dict[str, str] | None = None) -> object: ...


__all__ = ["Kaos"]
