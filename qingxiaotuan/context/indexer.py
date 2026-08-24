"""代码库索引器 —— 借鉴 Claude Code 的「把整个仓库装进上下文」。

扫描工作区, 产出一份紧凑、可读的地图: 目录树 + 各语言文件数/行数 +
被识别为"入口/关键"的文件。这份地图会钉在系统提示里, 让 Agent 在动手前
先对仓库全貌与架构有整体认知, 再按需 read_file 深入细节。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

# 这些目录默认不进入索引 (脚手架/产物/依赖)
_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".tox", ".eggs", "site-packages", ".idea", ".vscode",
    "target", "out", "bin", "obj",
}
# 被认为是"关键文件"的模式 (入口 / 约定 / 文档)
_KEY_FILES = {
    "readme": ("readme", "readme.md", "readme.txt", "readme.rst"),
    "main": ("main.py", "main.js", "main.ts", "index.js", "index.ts",
             "app.py", "app.js", "app.ts", "__main__.py", "main.go", "main.rs"),
    "config": ("package.json", "pyproject.toml", "setup.py", "setup.cfg",
               "cargo.toml", "go.mod", "requirements.txt", "pom.xml",
               "tsconfig.json", "Makefile", "dockerfile", "docker-compose.yml",
               ".github", "CMakeLists.txt"),
    "doc": ("readme.md", "docs", "doc"),
}
_LANG_MAP = {
    ".py": "Python", ".js": "JS", ".ts": "TS", ".tsx": "TSX", ".jsx": "JSX",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".c": "C", ".cpp": "C++",
    ".h": "C/H", ".hpp": "C++", ".cs": "C#", ".rb": "Ruby", ".sh": "Shell",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".md": "MD", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML", ".lua": "Lua", ".php": "PHP", ".swift": "Swift",
    ".kt": "Kotlin", ".vue": "Vue", ".zig": "Zig",
}

# 统计行数时单文件上限, 防止超大文件拖慢索引
_LOC_SAMPLE_CAP = 20000


@dataclass
class FileStat:
    path: str
    lang: str
    loc: int


@dataclass
class IndexResult:
    workspace: str
    total_files: int
    total_loc: int
    by_lang: Dict[str, Dict[str, int]] = field(default_factory=dict)
    key_files: List[str] = field(default_factory=list)
    tree: str = ""
    entries: List[FileStat] = field(default_factory=list)

    def map_text(self, max_tree_lines: int = 60) -> str:
        """生成钉进系统提示的紧凑地图文本。"""
        lang_lines = []
        for lang, st in sorted(self.by_lang.items(), key=lambda kv: -kv[1]["loc"]):
            lang_lines.append(f"  {lang}: {st['files']} 个文件 / {st['loc']} 行")
        stats = (
            f"## 代码库地图 ({Path(self.workspace).name})\n"
            f"- 规模: {self.total_files} 个文件, 约 {self.total_loc} 行代码\n"
            f"- 语言分布:\n" + ("\n".join(lang_lines) or "  (空)")
        )
        key = ""
        if self.key_files:
            key = "\n- 关键文件/入口: " + ", ".join(self.key_files[:12])
        tree_block = ""
        if self.tree:
            tlines = self.tree.splitlines()
            if len(tlines) > max_tree_lines:
                tlines = tlines[:max_tree_lines] + ["  … (已截断, 用 codebase_map 工具看完整)"]
            tree_block = "\n\n目录结构:\n" + "\n".join(tlines)
        return stats + key + tree_block


class CodebaseIndexer:
    """一次性、可缓存的工作区扫描器。"""

    def __init__(self, workspace: str, max_files: int = 300, max_loc: int = 200_000) -> None:
        self.workspace = str(workspace)
        self.max_files = max_files
        self.max_loc = max_loc
        self._cache: IndexResult | None = None

    def build(self, force: bool = False) -> IndexResult:
        if self._cache and not force:
            return self._cache
        root = Path(self.workspace)
        if not root.is_dir():
            return IndexResult(workspace=self.workspace, total_files=0, total_loc=0, tree="(工作区不存在)")

        entries: List[FileStat] = []
        by_lang: Dict[str, Dict[str, int]] = {}
        key_files: List[str] = []
        total_loc = 0
        scanned = 0

        # 先收集文件 (os.walk, 跳过噪声目录)
        walked: List[Tuple[str, List[str], List[str]]] = []
        for cur, dirs, files in os.walk(root):
            dirs[:] = [d for d in sorted(dirs) if d not in _SKIP_DIRS]
            walked.append((cur, dirs, files))

        for cur, dirs, files in walked:
            if scanned >= self.max_files:
                break
            for fname in sorted(files):
                if scanned >= self.max_files:
                    break
                ext = Path(fname).suffix.lower()
                rel = (Path(cur) / fname).relative_to(root).as_posix()
                lang = _LANG_MAP.get(ext, ext[1:].upper() if ext else "OTHER")
                try:
                    with open(Path(cur) / fname, "r", encoding="utf-8", errors="replace") as fh:
                        loc = sum(1 for _ in fh)
                except OSError:
                    loc = 0
                loc = min(loc, _LOC_SAMPLE_CAP)
                entries.append(FileStat(path=rel, lang=lang, loc=loc))
                total_loc += loc
                scanned += 1
                st = by_lang.setdefault(lang, {"files": 0, "loc": 0})
                st["files"] += 1
                st["loc"] += loc
                low = fname.lower()
                if low in _KEY_FILES["main"] or low in _KEY_FILES["config"] or low in _KEY_FILES["readme"]:
                    if rel not in key_files:
                        key_files.append(rel)

        tree = self._render_tree(root)
        self._cache = IndexResult(
            workspace=self.workspace,
            total_files=scanned,
            total_loc=total_loc,
            by_lang=by_lang,
            key_files=key_files,
            tree=tree,
            entries=entries,
        )
        return self._cache

    def _render_tree(self, root: Path, max_lines: int = 400) -> str:
        """生成带行数标注的目录树 (目录在前, 文件按行数排序)。"""
        lines: List[str] = []
        skip = _SKIP_DIRS

        def walk(p: Path, prefix: str) -> None:
            if len(lines) >= max_lines:
                return
            try:
                items = sorted(os.listdir(p))
            except OSError:
                return
            dirs = [d for d in items if (p / d).is_dir() and d not in skip]
            files = [f for f in items if not (p / f).is_dir()]
            # 文件按行数降序, 让"大文件"更显眼
            fstats = []
            for f in files:
                ext = Path(f).suffix.lower()
                lang = _LANG_MAP.get(ext, None)
                if lang is None:
                    continue
                try:
                    with open(p / f, "r", encoding="utf-8", errors="replace") as fh:
                        loc = min(sum(1 for _ in fh), _LOC_SAMPLE_CAP)
                except OSError:
                    loc = 0
                fstats.append((f, loc))
            fstats.sort(key=lambda x: -x[1])
            children = dirs + [f for f, _ in fstats]
            for i, name in enumerate(children):
                if len(lines) >= max_lines:
                    return
                last = i == len(children) - 1
                branch = "└── " if last else "├── "
                is_dir = (p / name).is_dir()
                if is_dir:
                    lines.append(f"{prefix}{branch}{name}/")
                    walk(p / name, prefix + ("    " if last else "│   "))
                else:
                    loc = dict(fstats).get(name, 0)
                    lines.append(f"{prefix}{branch}{name}  ({loc} 行)")

        lines.append(f"{root.name}/")
        walk(root, "")
        return "\n".join(lines)

    def find(self, pattern: str, limit: int = 20) -> List[FileStat]:
        """按文件名子串/扩展名快速检索文件 (供工具与 agent 用)。"""
        pat = pattern.lower()
        hits = [e for e in self._cache.entries if pat in e.path.lower()] if self._cache else []
        return hits[:limit]


def index_workspace(workspace: str, max_files: int = 300, max_loc: int = 200_000) -> IndexResult:
    return CodebaseIndexer(workspace, max_files=max_files, max_loc=max_loc).build()
