"""Workdir bucket name helpers for legacy session storage.

Legacy kimi-cli stored sessions under ``~/.kimi/sessions/<md5(workdirPath)>/``.
The migrator reverse-looks-up that md5 from ``kimi.json``'s ``work_dirs``.
"""

from __future__ import annotations

import hashlib


def old_md5_bucket_name(workdir_path: str) -> str:
    """Return the md5 hex of the workdir path (legacy bucket directory name)."""
    return hashlib.md5(workdir_path.encode("utf-8")).hexdigest()
