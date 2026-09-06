"""Kimi host and device identity header factories.

Ported from ``device.ts``. Pure-ish: the device id is persisted under ``homeDir``
using :mod:`pathlib` + :mod:`uuid` (stdlib), and device metadata is derived from
:mod:`os` / :mod:`platform` / :mod:`socket` rather than Node builtins. The
console-only ``macOsProductVersion`` exec path is approximated with
``_platform.mac_ver`` so nothing requires spawning a subprocess.
"""

from __future__ import annotations

import os
import platform as _platform
import re
import socket
import sys
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .types import DeviceHeaders

KIMI_CODE_PLATFORM = "kimi_code_cli"

KIMI_CODE_CUSTOM_HEADERS_ENV = "KIMI_CODE_CUSTOM_HEADERS"

_NON_ASCII_RE = re.compile(r"[^\x20-\x7e]")


@dataclass
class KimiHostIdentity:
    """Product name + host app version reported to the OAuth host."""

    product_name: str
    version: str
    platform: str
    user_agent_suffix: str | None = None


@dataclass
class KimiIdentityOptions(KimiHostIdentity):
    """Host identity plus the ``homeDir`` where the stable device id lives."""

    home_dir: str = ""


def ascii_header(value: str, fallback: str = "unknown") -> str:
    """Strip non-ASCII / control bytes and trim; fall back when emptied."""
    cleaned = _NON_ASCII_RE.sub("", value).strip()
    return cleaned if cleaned else fallback


def required_ascii_header(value: str, field_name: str) -> str:
    """Like :func:`ascii_header` but raise on an empty result."""
    cleaned = ascii_header(value, "")
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty ASCII string.")
    return cleaned


def parse_kimi_code_custom_headers(raw: str | None) -> dict[str, str]:
    """Parse ``KIMI_CODE_CUSTOM_HEADERS``-style ``Name: Value`` lines.

    Newline-separated; lines without a colon are skipped; names and values are
    trimmed. Pass ``None`` (or ``""``) for the unset environment value.
    """
    if not raw or not raw.strip():
        return {}
    headers: dict[str, str] = {}
    for line in raw.split("\n"):
        colon = line.find(":")
        if colon < 0:
            continue
        name = line[:colon].strip()
        if not name:
            continue
        headers[name] = line[colon + 1 :].strip()
    return headers


def create_kimi_user_agent(
    product_name: str,
    version: str,
    user_agent_suffix: str | None = None,
) -> str:
    """Build a ``Product/Version`` (optionally ``(suffix)``) User-Agent."""
    product = required_ascii_header(product_name, "Kimi identity product")
    ver = required_ascii_header(version, "Kimi identity version")
    suffix = None if user_agent_suffix is None else ascii_header(user_agent_suffix, "")
    if suffix:
        return f"{product}/{ver} ({suffix})"
    return f"{product}/{ver}"


def replace_user_agent_product(user_agent: str, product: str) -> str:
    """Swap the product token of a User-Agent, keeping version + suffix.

    ``kimi-code-cli/1.2.3 (web)`` -> ``acme/1.2.3 (web)``. A value without a
    ``/`` is treated as a bare product token and replaced wholesale.
    """
    cleaned = required_ascii_header(product, "Kimi identity product")
    separator = user_agent.find("/")
    return cleaned if separator < 0 else f"{cleaned}{user_agent[separator:]}"


def _device_model() -> str:
    if sys.platform == "darwin":
        release = _platform.mac_ver()[0] or _platform.release()
        return f"macOS {release} {_platform.machine()}"
    if sys.platform == "win32":
        return f"Windows {_platform.version()} {_platform.machine()}"
    system = _platform.system()
    if not system:
        return "unknown"
    return f"{system} {_platform.release()} {_platform.machine()}".strip()


def read_kimi_device_id(home_dir: str) -> str | None:
    """Return the persisted device id, or ``None`` if absent/unreadable."""
    path = os.path.join(home_dir, "device_id")
    if not os.path.exists(path):
        return None
    try:
        text = _read_text(path).strip()
        return text or None
    except OSError:
        return None


def create_kimi_device_id(
    home_dir: str,
    on_first_launch: Callable[[str], None] | None = None,
) -> str:
    """Return the persisted device id, minting + storing one on first use."""
    existing = read_kimi_device_id(home_dir)
    if existing is not None:
        return existing

    new_id = uuid.uuid4().hex
    try:
        os.makedirs(home_dir, exist_ok=True)
        _write_text(os.path.join(home_dir, "device_id"), new_id)
    except OSError:
        # Best-effort: requests can still use the in-memory id.
        pass
    if on_first_launch is not None:
        try:
            on_first_launch(new_id)
        except Exception:
            # Telemetry callback must not affect device id creation.
            pass
    return new_id


def create_kimi_device_headers(
    home_dir: str,
    version: str,
    platform: str,
) -> DeviceHeaders:
    """Build the ``X-Msh-*`` device header set for an outbound request."""
    return DeviceHeaders(
        x_msh_platform=required_ascii_header(platform, "Kimi identity platform"),
        x_msh_version=required_ascii_header(version, "Kimi identity version"),
        x_msh_device_name=ascii_header(socket.gethostname()),
        x_msh_device_model=ascii_header(_device_model()),
        x_msh_os_version=ascii_header(_platform.release()),
        x_msh_device_id=create_kimi_device_id(home_dir),
    )


def create_kimi_default_headers(options: KimiIdentityOptions) -> dict[str, str]:
    """Build the default request headers (User-Agent + device set)."""
    return {
        "User-Agent": create_kimi_user_agent(
            options.product_name,
            options.version,
            options.user_agent_suffix,
        ),
        **create_kimi_device_headers(
            home_dir=options.home_dir,
            version=options.version,
            platform=options.platform,
        ).__dict__,
    }


def assert_kimi_host_identity(identity: KimiHostIdentity | None) -> KimiHostIdentity:
    """Validate and return a host identity, raising if unset or malformed."""
    if identity is None:
        raise ValueError("Kimi host identity is required. Pass the host product name and version.")
    required_ascii_header(identity.product_name, "Kimi identity product")
    required_ascii_header(identity.version, "Kimi identity version")
    return identity


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


__all__ = [
    "KIMI_CODE_PLATFORM",
    "KIMI_CODE_CUSTOM_HEADERS_ENV",
    "KimiHostIdentity",
    "KimiIdentityOptions",
    "ascii_header",
    "required_ascii_header",
    "parse_kimi_code_custom_headers",
    "create_kimi_user_agent",
    "replace_user_agent_product",
    "read_kimi_device_id",
    "create_kimi_device_id",
    "create_kimi_device_headers",
    "create_kimi_default_headers",
    "assert_kimi_host_identity",
]
