"""配置子系统 —— 多层组合式配置。"""

from .defaults import DEFAULT_CONFIG, PRESET_PROFILES, load_builtin_soul
from .loader import (
    Config, deep_merge, patch_replace, _load_yaml, _chmod_600,
    load_dotenv, home_dir, dump_yaml, to_yaml_str,
)

__all__ = [
    "Config", "DEFAULT_CONFIG", "PRESET_PROFILES", "load_builtin_soul",
    "deep_merge", "patch_replace", "_load_yaml", "_chmod_600",
    "load_dotenv", "home_dir", "dump_yaml", "to_yaml_str",
]
