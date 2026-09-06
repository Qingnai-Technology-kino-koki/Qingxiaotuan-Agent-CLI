"""Managed Settings — 组织/用户/项目级设置级联 (对标 Claude Code 2.1.239 Managed Settings)。

设置优先级 (从高到低):
1. 环境变量 (QXT_*)
2. 项目级: .qingxiaotuan/settings.json
3. 用户级: ~/.qingxiaotuan/settings.json
4. 组织级: /etc/qingxiaotuan/managed-settings.json (Linux) 或 QXT_MANAGED_SETTINGS 环境变量
5. 默认值: qingxiaotuan/config/defaults.py

合并策略:
- dict 类型: 深合并 (项目级覆盖用户级的同名键)
- list 类型: 替换 (不合并)
- 标量: 高优先级覆盖低优先级
- 管理员可锁定某些设置 (locked_fields), 用户不可覆盖
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, cast


# ================================================================ 设置源

class SettingsSource:
    """单个设置源 (文件或环境变量)。"""

    def __init__(self, name: str, priority: int, data: Dict[str, Any]) -> None:
        self.name = name
        self.priority = priority   # 数值越大优先级越高
        self.data = data

    def __repr__(self) -> str:
        return f"SettingsSource({self.name!r}, priority={self.priority})"


# ================================================================ Managed Settings

class ManagedSettings:
    """设置级联管理器。

    用法:
        ms = ManagedSettings(Path("~/.qingxiaotuan"))
        config = ms.merge(project_dir=Path("/path/to/project"))
        # config 包含所有层级合并后的设置
    """

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self._sources: List[SettingsSource] = []
        self._locked_fields: Set[str] = set()
        self._load_all()

    def _load_all(self) -> None:
        """加载所有设置源。"""
        self._sources = []

        # 1. 默认值 (最低优先级)
        try:
            from .defaults import DEFAULT_CONFIG
            self._sources.append(SettingsSource("defaults", 0, DEFAULT_CONFIG))
        except ImportError:
            self._sources.append(SettingsSource("defaults", 0, {}))

        # 2. 组织级 (managed settings)
        org_data = self._load_org_settings()
        if org_data:
            self._sources.append(SettingsSource("organization", 10, org_data))
            # 提取锁定字段
            self._locked_fields.update(org_data.get("_locked_fields", []))

        # 3. 用户级 (~/.qingxiaotuan/settings.json)
        user_data = self._load_json(self.home / "settings.json")
        if user_data:
            self._sources.append(SettingsSource("user", 20, user_data))

        # 4. 环境变量 (最高优先级, 除锁定字段外)
        env_data = self._load_env()
        if env_data:
            self._sources.append(SettingsSource("environment", 30, env_data))

    def _load_org_settings(self) -> Dict[str, Any]:
        """加载组织级设置。"""
        # 检查环境变量
        env_path = os.environ.get("QXT_MANAGED_SETTINGS")
        if env_path:
            return self._load_json(Path(env_path))

        # 检查系统路径
        import platform
        if platform.system() == "Windows":
            candidates = [
                Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "qingxiaotuan" / "managed-settings.json",
            ]
        else:
            candidates = [
                Path("/etc/qingxiaotuan/managed-settings.json"),
                Path("/etc/xdg/qingxiaotuan/managed-settings.json"),
            ]

        for candidate in candidates:
            data = self._load_json(candidate)
            if data:
                return data
        return {}

    def _load_json(self, path: Path) -> Dict[str, Any]:
        """安全加载 JSON 文件。"""
        if not path.exists():
            return {}
        try:
            return cast(Dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return {}

    def _load_env(self) -> Dict[str, Any]:
        """从 QXT_* 环境变量加载设置。"""
        import logging
        data: Dict[str, Any] = {}
        prefix = "QXT_"
        for key, value in os.environ.items():
            if not key.startswith(prefix) or key == "QXT_HOME":
                continue
            # 守护: os.environ 在 Windows / 嵌入式容器 (systemd, WSL 转发)
            # 偶尔出现非 str 值 (e.g. bytes / list). 直接调 .lower() / .isdigit()
            # 会抛 AttributeError, 让整个配置加载失败。
            if not isinstance(value, str):
                logging.getLogger(__name__).warning(
                    "QXT_* 环境变量 %s 收到非 str 值 (%s), 已保留原值",
                    key, type(value).__name__,
                )
                data.setdefault("_env_warnings", []).append(key)
                continue
            # QXT_MODEL_PROVIDER=model.provider -> {"model": {"provider": value}}
            setting_key = key[len(prefix):].lower()
            parts = setting_key.split("_")
            current = data
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            # 尝试类型转换
            lowered = value.lower()
            if lowered in ("true", "yes", "on"):
                current[parts[-1]] = True
            elif lowered in ("false", "no", "off"):
                current[parts[-1]] = False
            elif value.isdigit():
                current[parts[-1]] = int(value)
            else:
                try:
                    current[parts[-1]] = float(value)
                except ValueError:
                    current[parts[-1]] = value
        return data

    def merge(self, project_dir: Optional[Path] = None) -> Dict[str, Any]:
        """合并所有设置源, 返回最终配置。

        项目级设置从 .qingxiaotuan/settings.json 加载 (最高优先级)。
        """
        sources = list(self._sources)

        # 项目级 (最高优先级, 除锁定字段外)
        if project_dir:
            project_settings_path = project_dir / ".qingxiaotuan" / "settings.json"
            project_data = self._load_json(project_settings_path)
            if project_data:
                sources.append(SettingsSource("project", 25, project_data))

        # 按优先级排序
        sources.sort(key=lambda s: s.priority)

        # 深合并
        result: Dict[str, Any] = {}
        for source in sources:
            self._deep_merge(result, source.data)

        return result

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """深合并: override 覆盖 base 的同名键。

        注意: 插入新键时必须深拷贝 value, 否则 base 会与原 source (如全局
        DEFAULT_CONFIG) 共享嵌套 dict 引用, 后续合并会就地污染全局默认值。
        """
        for key, value in override.items():
            # 锁定字段: 只有组织级可以设置, 其他级别不能覆盖
            if key in self._locked_fields:
                continue
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)
        return base

    def get(self, dotted: str, default: Any = None) -> Any:
        """按点号路径获取合并后的设置值。"""
        merged = self.merge()
        parts = dotted.split(".")
        current: Any = merged
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return default
            if current is None:
                return default
        return current

    def is_locked(self, field: str) -> bool:
        """检查字段是否被组织级锁定。"""
        return field in self._locked_fields

    def list_sources(self) -> List[Dict[str, Any]]:
        """列出所有活跃的设置源 (供 /status 展示)。"""
        return [
            {"name": s.name, "priority": s.priority, "key_count": len(s.data)}
            for s in self._sources
        ]

    def reload(self) -> None:
        """重新加载所有设置源 (配置变更后调用)。"""
        self._load_all()
