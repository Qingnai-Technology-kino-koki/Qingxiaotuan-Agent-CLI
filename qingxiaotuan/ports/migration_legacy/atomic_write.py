"""Atomic file writes: write to a temp sibling then rename over the target.

A crashed or interrupted write leaves the temp file behind but never a
partially-written target -- ``os.replace`` is atomic on POSIX. Use this for any
write that overwrites an existing user-owned file (e.g. migrated config/MCP
files that carry provider API keys).
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: str | os.PathLike[str], data: str) -> None:
    """Write ``data`` to ``path`` atomically.

    The content is first written to a temp file (``<name>.<pid>.tmp``) next to
    the target with 0600 permissions, then renamed over the target. On any
    failure the temp file is removed and the original target is left untouched.
    """
    p = Path(path)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    # The fd is opened 0600 so migrated secrets are never group/world-readable
    # even when the target home directory itself has permissive permissions.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            # Covers the case where a stale temp file from a crashed run already
            # exists with looser permissions.
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
    except BaseException:
        # Clean up the temp file so a failed write never leaves it behind.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
