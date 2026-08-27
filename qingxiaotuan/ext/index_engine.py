"""纯 Python 实现 index 引擎
FNV-1a 增量符号索引, 支持 path/lang/symbol 多维检索
符号带 kind (def/class/func/type) 与行号, 便于直接跳转
"""
import json
import sys
import os
import re

_LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".c": "c", ".h": "c", ".java": "java", ".go": "go",
    ".rs": "rust", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
}

# 各语言的符号提取规则: (正则, kind)。按行扫描以保留行号。
_SYMBOL_PATTERNS = {
    "python": [
        (re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"), "def"),
        (re.compile(r"^\s*class\s+([A-Za-z_]\w*)"), "class"),
    ],
    "javascript": [
        (re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)"), "func"),
        (re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)"), "class"),
    ],
    "go": [
        (re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), "func"),
        (re.compile(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)"), "type"),
    ],
    "rust": [
        (re.compile(r"\bfn\s+([a-z_]\w*)"), "fn"),
        (re.compile(r"\b(?:struct|enum|trait)\s+([A-Z]\w*)"), "type"),
    ],
    "java": [
        (re.compile(r"\b(?:class|interface|enum)\s+([A-Z]\w*)"), "type"),
    ],
}
_SYMBOL_PATTERNS["typescript"] = _SYMBOL_PATTERNS["javascript"]
# C 函数: 保留旧行为的近似匹配 (返回类型 + 名字 + 左括号)
_SYMBOL_PATTERNS["c"] = [(re.compile(r"^\w[\w\s\*]*\s+\**(\w+)\s*\("), "func")]


def _extract_symbols(content: str, lang: str):
    """按语言提取符号; 返回 (名字列表, [{name,kind,line} 列表])。"""
    details = []
    seen = set()
    for pattern_def in _SYMBOL_PATTERNS.get(lang, []):
        regex, kind = pattern_def
        for i, line in enumerate(content.splitlines(), 1):
            m = regex.search(line)
            if m:
                name = m.group(1)
                key = (name, kind, i)
                if key not in seen:
                    seen.add(key)
                    details.append({"name": name, "kind": kind, "line": i})
    names = list(dict.fromkeys(d["name"] for d in details))
    return names, details


class IndexEngine:
    """统一 JSONL IPC 协议的 index 引擎"""

    def __init__(self):
        self.methods = {
            "index": self.index,
            "search": self.search,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "index",
            "version": "1.1.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["fnv1a_hash", "symbol_index", "multidim_search",
                             "symbol_line_kind"],
        }

    @staticmethod
    def _fnv1a(text: str) -> int:
        """FNV-1a 哈希"""
        h = 0x811C9DC5
        for byte in text.encode():
            h = (h ^ byte) * 0x01000193
            h = h & 0xFFFFFFFF
        return h

    def index(self, params):
        """索引文件路径
        每个条目返回 path/lang/hash/symbols (名字列表, 向后兼容) 以及
        symbol_details ([{name,kind,line}], 可直接定位跳转)。
        """
        paths = params.get("paths", [])
        if isinstance(paths, str):
            paths = [paths]

        indexed = []
        for path in paths:
            if not os.path.exists(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            lang = _LANG_MAP.get(ext, "unknown")

            names, details = [], []
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                names, details = _extract_symbols(content, lang)
            except Exception:
                pass

            fp = self._fnv1a(path)
            indexed.append({
                "path": path,
                "lang": lang,
                "hash": hex(fp),
                "symbols": names,
                "symbol_details": details,
            })

        return {"indexed": indexed, "count": len(indexed)}

    def search(self, params):
        """检索符号"""
        query = params.get("query", "")
        lang = params.get("lang", None)
        path = params.get("path", None)
        index_data = params.get("index", [])

        results = []
        for item in index_data:
            if lang and item.get("lang") != lang:
                continue
            if path and path not in item.get("path", ""):
                continue
            if query and query.lower() not in [s.lower() for s in item.get("symbols", [])]:
                continue
            results.append(item)

        return {"results": results, "count": len(results)}

    def handle(self, line):
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id", None)
            if method in self.methods:
                result = self.methods[method](params)
                resp = {"id": req_id, "ok": True, "result": result}
            else:
                resp = {"id": req_id, "ok": False, "error": f"Unknown method: {method}"}
            return json.dumps(resp, ensure_ascii=False)
        except Exception as e:
            resp = {"id": None, "ok": False, "error": str(e)}
            return json.dumps(resp, ensure_ascii=False)

    def run(self):
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(self.handle(line) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    IndexEngine().run()
