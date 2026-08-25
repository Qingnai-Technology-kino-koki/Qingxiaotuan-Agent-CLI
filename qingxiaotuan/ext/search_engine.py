"""纯 Python 实现 search 引擎 (替代 ext/ts/src/search/main.ts)
递归正则检索, 忽略 node_modules/.git, 跳过二进制
"""
import json
import sys
import os
import re


class SearchEngine:
    """统一 JSONL IPC 协议的 search 引擎"""

    IGNORED_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache"}

    def __init__(self):
        self.methods = {
            "search": self.search,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "search",
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["recursive_regex_search", "file_filtering"],
        }

    def _is_binary(self, filepath: str) -> bool:
        """检查是否二进制文件"""
        try:
            with open(filepath, "rb") as f:
                chunk = f.read(1024)
                return b"\x00" in chunk
        except Exception:
            return True

    def search(self, params):
        """递归正则检索"""
        root = params.get("root", ".")
        pattern = params.get("pattern", "")
        max_results = params.get("max_results", 100)

        if not pattern:
            return {"results": [], "count": 0, "error": "No pattern provided"}

        results = []
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {"results": [], "count": 0, "error": f"Invalid regex: {e}"}

        for dirpath, dirnames, filenames in os.walk(root):
            # 过滤忽略目录
            dirnames[:] = [d for d in dirnames if d not in self.IGNORED_DIRS]

            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                # 跳过二进制
                if self._is_binary(filepath):
                    continue

                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append({
                                    "file": filepath,
                                    "line": line_num,
                                    "content": line.rstrip()[:500],
                                })
                                if len(results) >= max_results:
                                    return {"results": results, "count": len(results)}
                except Exception:
                    continue

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
    SearchEngine().run()
