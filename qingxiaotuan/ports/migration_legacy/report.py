"""Write the migration report as pretty-printed JSON to
``<targetHome>/migration-report.json``."""

from __future__ import annotations

import json
import os
from typing import Any

from .atomic_write import atomic_write
from .paths import migration_report_file


def write_report(target_home: str, report: dict[str, Any]) -> None:
    """Write ``report`` (a JSON-serializable mapping) to the target home."""
    os.makedirs(target_home, mode=0o700, exist_ok=True)
    atomic_write(
        migration_report_file(target_home), json.dumps(report, indent=2)
    )
