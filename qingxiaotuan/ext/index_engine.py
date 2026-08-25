"""纯 Python 实现 index 引擎 (替代 ext/c/index.c)
FNV-1a 增量符号索引, 支持 path/lang/symbol 多维检索
"""
import json
import sys
import os
import re


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
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["fnv1a_hash", "symbol_index", "multidim_search"],
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
        """索引文件路径"""
        paths = params.get("paths", [])
        if isinstance(paths, str):
            paths = [paths]

        indexed = []
        for path in paths:
            if not os.path.exists(path):
                continue
            # 判断语言
            ext = os.path.splitext(path)[1].lower()
            lang_map = {
                ".py": "python", ".js": "javascript", ".ts": "typescript",
                ".c": "c", ".h": "c", ".java": "java", ".go": "go",
                ".rs": "rust", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
            }
            lang = lang_map.get(ext, "unknown")

            # 提取符号 (简单正则)
            symbols = []
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                if lang == "python":
                    symbols.extend(re.findall(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)", content, re.M))
                    symbols.extend(re.findall(r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)", content, re.M))
                elif lang in ("javascript", "typescript"):
                    symbols.extend(re.findall(r"function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)", content))
                    symbols.extend(re.findall(r"class\s+([a-zA-Z_$][a-zA-Z0-9_$]*)", content))
                elif lang == "c":
                    symbols.extend(re.findall(r"^\w+\s+(\w+)\s*\(", content, re.M))
            except Exception:
                pass

            fp = self._fnv1a(path)
            indexed.append({
                "path": path,
                "lang": lang,
                "hash": hex(fp),
                "symbols": list(set(symbols)),
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
