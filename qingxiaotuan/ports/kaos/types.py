"""Data types mirrored from the TypeScript ``types.ts`` / ``process.ts`` modules.

``StatResult`` mirrors Python's ``os.stat_result`` fields. ``KaosProcess`` is
declared as a runtime-checkable :class:`typing.Protocol` so concrete backends
(local, SSH, container) can be substituted without a hard dependency on the
node stream types the original interface referenced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StatResult:
    """KAOS stat result, mirroring Python's ``os.stat_result`` fields."""

    st_mode: int
    st_ino: int
    st_dev: int
    st_nlink: int
    st_uid: int
    st_gid: int
    st_size: int
    st_atime: float
    st_mtime: float
    st_ctime: float


@runtime_checkable
class KaosProcess(Protocol):
    """A running process spawned by a :class:`~qingxiaotuan.ports.kaos.kaos.Kaos`.

    The original interface was bound to node ``stream.Readable``/``Writable``
    objects, which have no stdlib Python equivalent; this protocol keeps the
    lifecycle surface (pid / wait / kill / dispose) that callers depend on.
    """

    pid: int
    exit_code: int | None

    def wait(self) -> int: ...

    def kill(self, signal: str | int | None = None) -> None: ...


__all__ = ["StatResult", "KaosProcess"]
