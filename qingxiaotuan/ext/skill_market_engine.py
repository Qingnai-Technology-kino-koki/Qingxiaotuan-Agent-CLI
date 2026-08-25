"""纯 Python 实现 skill-market 引擎 (替代 ext/ts/src/skill-market/main.ts)

从技能 registry (本地目录) 拉取与发布技能包。一个技能包 = 目录, 含
SKILL.md (frontmatter: name, description, version) + 附件。

默认 registry: ~/.qingxiaotuan/skill-market (可用 QXT_SKILL_REGISTRY 覆盖)。

IPC 方法:
  list     { registry? }    -> { packages:[{name,version,description}] }
  info     { name }         -> { package }
  install  { name, target } -> { ok, path }
  publish  { source, registry? } -> { ok }
  search   { query }        -> { packages }
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

_FRONT_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def _default_registry() -> Path:
    env = os.environ.get("QXT_SKILL_REGISTRY")
    if env:
        return Path(env)
    return Path.home() / ".qingxiaotuan" / "skill-market"


def _parse_frontmatter(text: str) -> dict:
    meta = {}
    m = _FRONT_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^(\w+):\s*(.*)$", line)
            if kv:
                meta[kv.group(1)] = kv.group(2)
    return meta


def _read_skill_meta(dir_path: Path):
    skill_md = dir_path / "SKILL.md"
    if not skill_md.exists():
        return None
    meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    return {
        "name": meta.get("name") or dir_path.name,
        "description": meta.get("description", ""),
        "version": meta.get("version", "0.1.0"),
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
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "skill-market",
            "version": "1.0.0-python",
            "methods": ["list", "search", "info", "install", "publish"],
        }

    def _registry(self, params) -> Path:
        return Path(str(params.get("registry") or _default_registry()))

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
        if not source.is_dir() or not (source / "SKILL.md").exists():
            raise ValueError("source has no SKILL.md")
        meta = _read_skill_meta(source)
        reg = self._registry(params)
        reg.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, reg / meta["name"], dirs_exist_ok=True)
        return {"ok": True, "name": meta["name"]}

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
