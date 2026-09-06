"""Normalize a git remote URL into a host/path identifier.

自研实现 (对齐上游线协议; 零依赖, 纯逻辑)。
"""
from __future__ import annotations

import re

_SCP_LIKE = re.compile(r"^(?:[^@\s]+@)?([^:\s]+):(.+)$")


def normalize_remote(url: str) -> str:
    trimmed = url.strip()
    if trimmed == "":
        return ""

    if "://" not in trimmed:
        scp_like = _SCP_LIKE.match(trimmed)
        if scp_like is not None:
            return _join_remote_parts(scp_like.group(1), None, scp_like.group(2))
        return _strip_git_suffix(trimmed)

    try:
        from urllib.parse import urlparse

        parsed = urlparse(trimmed)
    except ValueError:
        return _strip_git_suffix(trimmed)

    if parsed.hostname is None or parsed.hostname == "":
        return _strip_git_suffix(trimmed)
    return _join_remote_parts(parsed.hostname, parsed.port, parsed.path)


def _join_remote_parts(
    host: str | None,
    port: str | int | None,
    path: str | None,
) -> str:
    normalized_host = (host or "").strip()
    normalized_port = "" if port is None else str(port).strip()
    normalized_path = _strip_git_suffix(path or "")
    parts = [normalized_host]
    if normalized_port:
        parts.append(normalized_port)
    if normalized_path:
        parts.append(normalized_path)
    return "/".join(parts)


def _strip_git_suffix(value: str) -> str:
    normalized = value.strip().strip("/")
    if normalized.endswith(".git"):
        normalized = normalized[: -len(".git")]
    return normalized


__all__ = ["normalize_remote"]
