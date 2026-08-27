"""纯 Python 实现 skill-market 引擎 v2 — Universal Skill Marketplace

核心增强:
- Universal Format Adapter: 从任意 Agent 项目导入技能 (SKILL.md / .cursorrules / CLAUDE.md / AGENTS.md / .windsurfrules / .clinerules / copilot-instructions)
- Plugin SDK: plugin.json manifest, headersHelper, lifecycle hooks, skill namespace
- Import/Export: 跨 Agent 迁移, 批量导入, 标准化输出
- Plugin Marketplace: install/update/enable/disable, 版本管理, 依赖解析

IPC 方法:
  list         { registry? }                    -> { packages }
  search       { query }                        -> { packages }
  info         { name }                         -> { package }
  install      { name, target }                 -> { ok, path }
  publish      { source, registry? }            -> { ok }
  import       { source, format?, output? }     -> { ok, packages }
  export       { name, format?, output? }       -> { ok, path }
  detect       { path }                         -> { format, packages }
  plugin_list  { }                              -> { plugins }
  plugin_install { source }                     -> { ok, plugin }
  plugin_uninstall { name }                     -> { ok }
  plugin_enable  { name }                       -> { ok }
  plugin_disable { name }                       -> { ok }
  _meta/list                                   -> { engine, version, methods }
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_FRONT_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def _default_registry() -> Path:
    env = os.environ.get("QXT_SKILL_REGISTRY")
    if env:
        return Path(env)
    home = os.environ.get("QXT_HOME")
    if home:
        return Path(home) / "skill-market"
    return Path.home() / ".qingxiaotuan" / "skill-market"


def _parse_frontmatter(text: str) -> dict:
    meta = {}
    m = _FRONT_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
            if kv:
                meta[kv.group(1)] = kv.group(2).strip()
    return meta


def _read_skill_meta(dir_path: Path) -> Optional[dict]:
    skill_md = dir_path / "SKILL.md"
    if not skill_md.exists():
        return None
    meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    return {
        "name": meta.get("name") or dir_path.name,
        "description": meta.get("description", ""),
        "version": meta.get("version", "0.1.0"),
        "source_format": "skill_md",
    }


def _list_packages(registry: Path) -> list:
    if not registry.is_dir():
        return []
    out = []
    for entry in sorted(registry.iterdir()):
        if not entry.is_dir():
            continue
        meta = _read_skill_meta(entry)
        if meta:
            out.append(meta)
    return out


class SkillMarketEngine:
    def __init__(self):
        self.methods = {
            "list": self.list,
            "search": self.search,
            "info": self.info,
            "install": self.install,
            "publish": self.publish,
            "import": self.import_skills,
            "export": self.export_skill,
            "detect": self.detect_format,
            "plugin_list": self.plugin_list,
            "plugin_install": self.plugin_install,
            "plugin_uninstall": self.plugin_uninstall,
            "plugin_enable": self.plugin_enable,
            "plugin_disable": self.plugin_disable,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "skill-market",
            "version": "2.0.0-python",
            "methods": [
                "list", "search", "info", "install", "publish",
                "import", "export", "detect",
                "plugin_list", "plugin_install", "plugin_uninstall",
                "plugin_enable", "plugin_disable",
            ],
        }

    def _registry(self, params) -> Path:
        return Path(str(params.get("registry") or _default_registry()))

    # ================================================================ 技能市场

    def list(self, params):
        reg = self._registry(params)
        return {"registry": str(reg), "packages": _list_packages(reg)}

    def search(self, params):
        q = str(params.get("query", "")).lower()
        reg = self._registry(params)
        pkgs = [p for p in _list_packages(reg)
                if q in p["name"].lower() or q in p["description"].lower()]
        return {"packages": pkgs}

    def info(self, params):
        name = str(params.get("name", ""))
        reg = self._registry(params)
        meta = _read_skill_meta(reg / name)
        if not meta:
            raise ValueError(f"skill not found: {name}")
        # 读取完整 SKILL.md 内容
        skill_md = reg / name / "SKILL.md"
        meta["body"] = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""
        # 检查是否有 plugin.json
        plugin_json = reg / name / "plugin.json"
        if plugin_json.exists():
            meta["has_plugin_manifest"] = True
        return {"package": meta, "path": str(reg / name)}

    def install(self, params):
        name = str(params.get("name", ""))
        reg = self._registry(params)
        src = reg / name
        if not src.is_dir() or not (src / "SKILL.md").exists():
            raise ValueError(f"skill not found: {name}")
        target = Path(str(params.get("target") or (reg.parent / "skills")))
        dst = target / name
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return {"ok": True, "path": str(dst)}

    def publish(self, params):
        source = Path(str(params.get("source", "")))
        if not source.is_dir():
            raise ValueError("source is not a directory")
        # 支持 plugin.json manifest
        plugin_json = source / "plugin.json"
        if plugin_json.exists():
            data = json.loads(plugin_json.read_text(encoding="utf-8"))
            name = data.get("name", source.name)
        else:
            skill_md = source / "SKILL.md"
            if not skill_md.exists():
                raise ValueError("source has no SKILL.md or plugin.json")
            meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            name = meta.get("name") or source.name
        reg = self._registry(params)
        reg.mkdir(parents=True, exist_ok=True)
        target = reg / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        return {"ok": True, "name": name}

    # ================================================================ Universal Import/Export

    def import_skills(self, params):
        """从任意 Agent 项目导入技能。

        params:
            source: 源路径 (目录或文件)
            format: 可选, 强制指定格式 (skill_md/cursorrules/claude_md/agents_md/...)
            output: 输出目录 (默认 registry)
            overwrite: 是否覆盖已有 (默认 false)
        """
        source = Path(str(params.get("source", ""))).resolve()
        output = Path(str(params.get("output", ""))) or self._registry(params)
        output = Path(output)
        overwrite = params.get("overwrite", False)

        if not source.exists():
            raise ValueError(f"source not found: {source}")

        # 使用 Universal Format Adapter
        try:
            from ..skills.universal_format import (
                parse_skill_file, parse_skill_directory, import_from_agent_project,
                SkillPackage,
            )
        except ImportError:
            from skills.universal_format import (
                parse_skill_file, parse_skill_directory, import_from_agent_project,
                SkillPackage,
            )

        imported: List[Dict[str, Any]] = []

        if source.is_file():
            pkg = parse_skill_file(source)
            if pkg:
                slug = SkillPackage._sanitize_name(pkg.name)
                out_dir = output / slug
                if out_dir.exists() and not overwrite:
                    return {"ok": True, "packages": [], "skipped": [pkg.name]}
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "SKILL.md").write_text(pkg.to_skill_md(), encoding="utf-8")
                imported.append({"name": pkg.name, "format": pkg.source_format, "path": str(out_dir)})
        else:
            # 目录: 尝试从 Agent 项目导入
            pkgs = import_from_agent_project(source)
            for pkg in pkgs:
                slug = SkillPackage._sanitize_name(pkg.name)
                out_dir = output / slug
                if out_dir.exists() and not overwrite:
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "SKILL.md").write_text(pkg.to_skill_md(), encoding="utf-8")
                imported.append({"name": pkg.name, "format": pkg.source_format, "path": str(out_dir)})

        return {"ok": True, "packages": imported, "count": len(imported)}

    def export_skill(self, params):
        """导出技能为标准 SKILL.md 格式。

        params:
            name: 技能名
            output: 输出路径 (默认当前目录)
        """
        name = str(params.get("name", ""))
        reg = self._registry(params)
        src = reg / name
        if not src.is_dir():
            raise ValueError(f"skill not found: {name}")

        output = Path(str(params.get("output", "."))).resolve()
        dst = output / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return {"ok": True, "path": str(dst), "name": name}

    def detect_format(self, params):
        """检测文件/目录的技能格式。

        params:
            path: 文件或目录路径
        """
        target = Path(str(params.get("path", ""))).resolve()
        if not target.exists():
            raise ValueError(f"path not found: {target}")

        try:
            from ..skills.universal_format import (
                detect_format, parse_skill_file, parse_skill_directory,
                FORMAT_INFO,
            )
        except ImportError:
            from skills.universal_format import (
                detect_format, parse_skill_file, parse_skill_directory,
                FORMAT_INFO,
            )

        if target.is_file():
            fmt = detect_format(target)
            pkg = parse_skill_file(target)
            return {
                "path": str(target),
                "format": fmt,
                "format_info": FORMAT_INFO.get(fmt, {}),
                "packages": [{"name": pkg.name, "description": pkg.description}] if pkg else [],
            }
        else:
            pkgs = parse_skill_directory(target)
            formats = list(set(p.source_format for p in pkgs))
            return {
                "path": str(target),
                "formats": formats,
                "format_info": {f: FORMAT_INFO.get(f, {}) for f in formats},
                "packages": [{"name": p.name, "format": p.source_format, "description": p.description} for p in pkgs],
                "count": len(pkgs),
            }

    # ================================================================ Plugin Marketplace

    def plugin_list(self, params):
        """列出所有已安装插件。"""
        try:
            from ..skills.plugin_sdk import PluginManager
        except ImportError:
            from skills.plugin_sdk import PluginManager

        plugins_dir = self._registry(params).parent / "plugins"
        if not plugins_dir.is_dir():
            plugins_dir = self._registry(params)  # fallback
        pm = PluginManager(plugins_dir)
        plugins = []
        for info in pm.list_plugins():
            plugins.append({
                "name": info.manifest.name,
                "version": info.manifest.version,
                "description": info.manifest.description,
                "enabled": info.enabled,
                "skills": info.skill_names(),
                "source": info.source,
            })
        return {"plugins": plugins, "count": len(plugins)}

    def plugin_install(self, params):
        """安装插件。"""
        source = Path(str(params.get("source", ""))).resolve()
        if not source.exists():
            raise ValueError(f"source not found: {source}")

        try:
            from ..skills.plugin_sdk import PluginManager
        except ImportError:
            from skills.plugin_sdk import PluginManager

        plugins_dir = self._registry(params).parent / "plugins"
        pm = PluginManager(plugins_dir)
        info = pm.install_from_directory(source)
        return {
            "ok": True,
            "plugin": {
                "name": info.manifest.name,
                "version": info.manifest.version,
                "skills": info.skill_names(),
            },
        }

    def plugin_uninstall(self, params):
        name = str(params.get("name", ""))
        try:
            from ..skills.plugin_sdk import PluginManager
        except ImportError:
            from skills.plugin_sdk import PluginManager

        plugins_dir = self._registry(params).parent / "plugins"
        pm = PluginManager(plugins_dir)
        ok = pm.uninstall(name)
        return {"ok": ok, "name": name}

    def plugin_enable(self, params):
        name = str(params.get("name", ""))
        try:
            from ..skills.plugin_sdk import PluginManager
        except ImportError:
            from skills.plugin_sdk import PluginManager

        plugins_dir = self._registry(params).parent / "plugins"
        pm = PluginManager(plugins_dir)
        ok = pm.enable(name)
        return {"ok": ok, "name": name}

    def plugin_disable(self, params):
        name = str(params.get("name", ""))
        try:
            from ..skills.plugin_sdk import PluginManager
        except ImportError:
            from skills.plugin_sdk import PluginManager

        plugins_dir = self._registry(params).parent / "plugins"
        pm = PluginManager(plugins_dir)
        ok = pm.disable(name)
        return {"ok": ok, "name": name}

    # ================================================================ IPC Handler

    def handle(self, line: str) -> str:
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {}) or {}
            req_id = req.get("id")
            if method in self.methods:
                result = self.methods[method](params)
                return json.dumps({"id": req_id, "ok": True, "result": result}, ensure_ascii=False)
            return json.dumps({"id": req_id, "ok": False,
                               "error": f"Unknown method: {method}"}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"id": None, "ok": False, "error": str(exc)}, ensure_ascii=False)

    def run(self) -> None:
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(self.handle(line) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    SkillMarketEngine().run()
