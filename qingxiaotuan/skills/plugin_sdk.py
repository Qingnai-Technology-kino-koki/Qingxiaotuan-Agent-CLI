"""Plugin SDK — 插件化技能包的标准化管理。

对标 Claude Code 2.1.239 Plugin Marketplace:
- plugin.json manifest: 声明插件元数据、依赖、技能列表、钩子
- headersHelper: 自定义 HTTP 头 (支持短生命周期 token)
- 技能命名空间: plugin-name:skill-name (避免冲突)
- 生命周期钩子: install / uninstall / enable / disable
- 版本管理: semver 兼容性检查
- 依赖解析: 插件间依赖声明
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ================================================================ 数据结构

@dataclass
class PluginManifest:
    """plugin.json 的标准化表示。"""
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    license: str = ""
    repository: str = ""
    # 技能列表: [{name, description, path, auto_invoke}]
    skills: List[Dict[str, Any]] = field(default_factory=list)
    # 钩子: {install, uninstall, enable, disable} — shell 命令或脚本路径
    hooks: Dict[str, str] = field(default_factory=dict)
    # 依赖: {plugin-name: version-range}
    dependencies: Dict[str, str] = field(default_factory=dict)
    # 兼容性: {min_qxt_version, platforms, requires}
    compatibility: Dict[str, str] = field(default_factory=dict)
    # headersHelper: 生成 HTTP 头的命令
    headers_helper: str = ""
    # 元数据: 任意键值对
    metadata: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "0.1.0")),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            license=str(data.get("license", "")),
            repository=str(data.get("repository", "")),
            skills=data.get("skills", []) or [],
            hooks=data.get("hooks", {}) or {},
            dependencies=data.get("dependencies", {}) or {},
            compatibility=data.get("compatibility", {}) or {},
            headers_helper=str(data.get("headersHelper", "")),
            metadata=data.get("metadata", {}) or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "version": self.version,
        }
        if self.description:
            d["description"] = self.description
        if self.author:
            d["author"] = self.author
        if self.license:
            d["license"] = self.license
        if self.repository:
            d["repository"] = self.repository
        if self.skills:
            d["skills"] = self.skills
        if self.hooks:
            d["hooks"] = self.hooks
        if self.dependencies:
            d["dependencies"] = self.dependencies
        if self.compatibility:
            d["compatibility"] = self.compatibility
        if self.headers_helper:
            d["headersHelper"] = self.headers_helper
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class PluginInfo:
    """已安装插件的运行时信息。"""
    manifest: PluginManifest
    path: Path              # 插件目录
    enabled: bool = True
    installed_at: float = 0.0
    updated_at: float = 0.0
    source: str = "local"   # local | marketplace | git | url

    @property
    def qualified_name(self) -> str:
        return self.manifest.name

    def skill_names(self) -> List[str]:
        """返回所有技能的限定名 (plugin:skill 格式)。"""
        return [
            f"{self.manifest.name}:{s['name']}"
            for s in self.manifest.skills
            if s.get("name")
        ]


# ================================================================ Semver 兼容性

def _parse_version(v: str) -> Tuple[int, int, int]:
    """解析 semver 版本号。"""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v.strip())
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (0, 0, 0)


def _version_satisfies(current: str, requirement: str) -> bool:
    """检查版本是否满足要求 (支持 ^~>= 等)。"""
    cur = _parse_version(current)
    req = requirement.strip()
    if req.startswith("^"):
        # ^1.2.3: >=1.2.3, <2.0.0
        base = _parse_version(req[1:])
        return cur >= base and cur[0] == base[0]
    elif req.startswith("~"):
        # ~1.2.3: >=1.2.3, <1.3.0
        base = _parse_version(req[1:])
        return cur >= base and cur[0] == base[0] and cur[1] == base[1]
    elif req.startswith(">="):
        return cur >= _parse_version(req[2:])
    elif req.startswith(">"):
        return cur > _parse_version(req[1:])
    elif req.startswith("<"):
        return cur < _parse_version(req[1:])
    elif req.startswith("=") or req.startswith("=="):
        return cur == _parse_version(req.lstrip("="))
    else:
        # 精确匹配
        return cur == _parse_version(req)


# ================================================================ Plugin Manager

class PluginManager:
    """插件管理器 — 安装/卸载/启用/禁用/更新插件。

    插件存储结构:
        <plugins_dir>/
        ├── my-plugin/
        │   ├── plugin.json
        │   ├── SKILL.md (或 skills/*/SKILL.md)
        │   ├── scripts/
        │   └── ...
        └── another-plugin/
            └── ...
    """

    def __init__(self, plugins_dir: Path) -> None:
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, PluginInfo] = {}
        self._scan()

    def _scan(self) -> None:
        """扫描已安装插件。"""
        self._cache.clear()
        if not self.plugins_dir.is_dir():
            return
        for entry in sorted(self.plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "plugin.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = PluginManifest.from_dict(data)
                enabled_path = entry / ".enabled"
                enabled = not enabled_path.exists() or enabled_path.read_text(encoding="utf-8").strip() != "false"
                self._cache[manifest.name] = PluginInfo(
                    manifest=manifest,
                    path=entry,
                    enabled=enabled,
                    installed_at=entry.stat().st_mtime,
                    updated_at=manifest_path.stat().st_mtime,
                )
            except Exception:
                continue

    def list_plugins(self) -> List[PluginInfo]:
        """列出所有已安装插件。"""
        return list(self._cache.values())

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        """获取指定插件信息。"""
        return self._cache.get(name)

    def install_from_directory(self, source: Path, name: Optional[str] = None) -> PluginInfo:
        """从本地目录安装插件。"""
        source = Path(source).resolve()
        manifest_path = source / "plugin.json"
        if not manifest_path.exists():
            raise ValueError(f"目录 {source} 中没有 plugin.json")

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = PluginManifest.from_dict(data)
        plugin_name = name or manifest.name
        if not plugin_name:
            raise ValueError("插件名称不能为空")

        # 检查依赖
        self._check_dependencies(manifest)

        target = self.plugins_dir / plugin_name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, dirs_exist_ok=True)

        # 写入启用标记
        (target / ".enabled").write_text("true", encoding="utf-8")

        # 执行 install hook
        self._run_hook(target, manifest, "install")

        # 刷新缓存
        self._scan()
        info = self._cache.get(plugin_name)
        if info is None:
            raise RuntimeError(f"安装后未找到插件 {plugin_name}")
        info.source = "local"
        return info

    def uninstall(self, name: str) -> bool:
        """卸载插件。"""
        info = self._cache.get(name)
        if info is None:
            return False

        # 执行 uninstall hook
        self._run_hook(info.path, info.manifest, "uninstall")

        shutil.rmtree(info.path, ignore_errors=True)
        self._cache.pop(name, None)
        return True

    def enable(self, name: str) -> bool:
        """启用插件。"""
        info = self._cache.get(name)
        if info is None:
            return False
        (info.path / ".enabled").write_text("true", encoding="utf-8")
        info.enabled = True
        self._run_hook(info.path, info.manifest, "enable")
        return True

    def disable(self, name: str) -> bool:
        """禁用插件。"""
        info = self._cache.get(name)
        if info is None:
            return False
        (info.path / ".enabled").write_text("false", encoding="utf-8")
        info.enabled = False
        self._run_hook(info.path, info.manifest, "disable")
        return True

    def get_all_skills(self) -> List[Dict[str, Any]]:
        """获取所有已启用插件的技能列表。"""
        skills = []
        for info in self._cache.values():
            if not info.enabled:
                continue
            for skill in info.manifest.skills:
                skill_path = info.path / skill.get("path", "SKILL.md")
                if not skill_path.exists():
                    skill_path = info.path / "SKILL.md"
                skills.append({
                    "name": f"{info.manifest.name}:{skill.get('name', 'unknown')}",
                    "description": skill.get("description", ""),
                    "path": str(skill_path),
                    "auto_invoke": skill.get("auto_invoke", False),
                    "plugin": info.manifest.name,
                    "plugin_version": info.manifest.version,
                })
        return skills

    def resolve_skill(self, qualified_name: str) -> Optional[Dict[str, Any]]:
        """解析限定技能名 (plugin:skill) → 技能信息。"""
        if ":" not in qualified_name:
            return None
        plugin_name, skill_name = qualified_name.split(":", 1)
        info = self._cache.get(plugin_name)
        if info is None or not info.enabled:
            return None
        for skill in info.manifest.skills:
            if skill.get("name") == skill_name:
                skill_path = info.path / skill.get("path", "SKILL.md")
                if not skill_path.exists():
                    skill_path = info.path / "SKILL.md"
                return {
                    "name": qualified_name,
                    "description": skill.get("description", ""),
                    "path": str(skill_path),
                    "body": skill_path.read_text(encoding="utf-8") if skill_path.exists() else "",
                    "plugin": plugin_name,
                }
        return None

    def _check_dependencies(self, manifest: PluginManifest) -> None:
        """检查插件依赖是否满足。"""
        for dep_name, dep_version in manifest.dependencies.items():
            dep_info = self._cache.get(dep_name)
            if dep_info is None:
                raise ValueError(f"缺少依赖插件: {dep_name} (需要 {dep_version})")
            if not _version_satisfies(dep_info.manifest.version, dep_version):
                raise ValueError(
                    f"依赖插件 {dep_name} 版本不满足: "
                    f"已安装 {dep_info.manifest.version}, 需要 {dep_version}"
                )

    def _run_hook(self, plugin_path: Path, manifest: PluginManifest, hook_name: str) -> None:
        """执行插件生命周期钩子。"""
        hook_cmd = manifest.hooks.get(hook_name)
        if not hook_cmd:
            return
        try:
            import subprocess
            subprocess.run(
                hook_cmd,
                shell=True,
                cwd=str(plugin_path),
                timeout=30,
                capture_output=True,
            )
        except Exception:
            pass  # 钩子失败不阻断主流程


# ================================================================ Headers Helper

class HeadersHelper:
    """自定义 HTTP 头生成器 (对标 Claude Code headersHelper)。

    用于插件市场需要认证的场景: 指定一个命令, 该命令输出 JSON 格式的 HTTP 头。
    """

    @staticmethod
    def resolve(command: str, cwd: Optional[Path] = None, timeout: int = 10) -> Dict[str, str]:
        """执行 headersHelper 命令, 返回 HTTP 头字典。"""
        if not command.strip():
            return {}
        try:
            import subprocess
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd) if cwd else None,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return {}
            output = result.stdout.strip()
            if not output:
                return {}
            # 尝试解析为 JSON
            try:
                headers = json.loads(output)
                if isinstance(headers, dict):
                    return {str(k): str(v) for k, v in headers.items()}
            except json.JSONDecodeError:
                # 尝试解析为 "Key: Value" 格式
                headers = {}
                for line in output.splitlines():
                    if ": " in line:
                        k, v = line.split(": ", 1)
                        headers[k.strip()] = v.strip()
                return headers
        except Exception:
            pass
        return {}

    @staticmethod
    def safe_resolve(command: str, cwd: Optional[Path] = None) -> Dict[str, str]:
        """安全解析: 不继承凭证环境变量, 仅从指定目录执行。"""
        # 清除敏感环境变量
        clean_env = {k: v for k, v in os.environ.items()
                     if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH"))}
        try:
            import subprocess
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd) if cwd else None,
                timeout=10,
                capture_output=True,
                text=True,
                env=clean_env,
            )
            if result.returncode != 0:
                return {}
            headers = json.loads(result.stdout.strip())
            if isinstance(headers, dict):
                return {str(k): str(v) for k, v in headers.items()}
        except Exception:
            pass
        return {}
