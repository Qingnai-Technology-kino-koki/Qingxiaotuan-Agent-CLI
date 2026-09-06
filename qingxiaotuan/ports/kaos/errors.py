"""Base error classes for the kaos port.

Mirrors the TypeScript ``errors.ts`` module. ``KaosError`` is the base
class; the rest are semantic subclasses mapping onto familiar Python
builtins so callers can ``except`` precisely.
"""

from __future__ import annotations


class KaosError(Exception):
    """Base error class for the kaos package."""


class KaosValueError(KaosError):
    """Equivalent to Python's ``ValueError`` — an invalid argument was passed."""


class KaosFileExistsError(KaosError):
    """Equivalent to Python's ``FileExistsError``."""


class KaosShellNotFoundError(KaosError):
    """Raised when no Git Bash install can be located on a Windows host.

    Carries the list of probed paths so callers can build install hints.
    """


__all__ = [
    "KaosError",
    "KaosValueError",
    "KaosFileExistsError",
    "KaosShellNotFoundError",
]
