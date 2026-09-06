"""Detect whether a target config.toml / tui.toml is the unmodified default
stub (or missing) -- i.e. safe to overwrite during migration.

The default text constants are verbatim from the upstream reference implementation so a
fresh install's stub file compares byte-for-byte. The TUI check additionally
falls back to a semantic comparison after parsing with :mod:`tomllib`.
"""

from __future__ import annotations

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _toml = None  # type: ignore[assignment]

DEFAULT_CONFIG_FILE_TEXT = (
    "# ~/.kimi-code/config.toml\n"
    "# Runtime settings for Kimi Code.\n"
    "# This file starts empty so built-in defaults can apply.\n"
    "# Login will populate managed Kimi provider and model entries.\n"
)

DEFAULT_TUI_RENDER = (
    "# ~/.kimi-code/tui.toml\n"
    "# Terminal UI preferences for kimi-code.\n"
    "# Agent/runtime settings stay in ~/.kimi-code/config.toml.\n"
    "\n"
    'theme = "auto" # "auto" | "dark" | "light"\n'
    "\n"
    "[editor]\n"
    'command = "" # Empty uses $VISUAL / $EDITOR\n'
    "\n"
    "[notifications]\n"
    'enabled = true # true | false\n'
    'notification_condition = "unfocused" # "unfocused" | "always"\n'
)


def is_config_stub_or_missing(config_path: str) -> bool:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return True  # missing = ok to overwrite
    return text == DEFAULT_CONFIG_FILE_TEXT


def is_tui_stub_or_missing(tui_path: str) -> bool:
    try:
        with open(tui_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return True

    if text == DEFAULT_TUI_RENDER:
        return True

    # Fallback: parse and compare fields semantically.
    if _toml is None:
        # No TOML parser available (Python < 3.11 and tomli not installed).
        # Cannot do a semantic compare -> conservatively treat as NOT a stub
        # so the migration never overwrites a user-modified file.
        return False
    try:
        parsed = _toml.loads(text)
    except Exception:
        return False  # unparseable = user-modified, do not overwrite

    theme = parsed.get("theme")
    editor = parsed.get("editor")
    notifications = parsed.get("notifications")

    theme_ok = theme is None or theme == "auto"
    if editor is None or not isinstance(editor, dict):
        editor_ok = editor is None
    else:
        editor_ok = ("command" not in editor) or (editor["command"] == "")
    if notifications is None or not isinstance(notifications, dict):
        notif_enabled_ok = notifications is None
        notif_cond_ok = notifications is None
    else:
        notif_enabled_ok = (
            "enabled" not in notifications
        ) or notifications["enabled"] is True
        notif_cond_ok = (
            "notification_condition" not in notifications
        ) or notifications["notification_condition"] == "unfocused"

    return bool(theme_ok and editor_ok and notif_enabled_ok and notif_cond_ok)
