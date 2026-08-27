"""纯 Python 实现 search 引擎
递归正则检索, 忽略 node_modules/.git, 跳过二进制
支持 glob 包含/排除过滤、忽略大小写、上下文行、按文件计数
"""
import json
import sys
import os
import re
from fnmatch import fnmatch

# 上下文行上限: 防止单条结果撑爆 IPC 响应
_MAX_CONTEXT = 10


class SearchEngine:
    """统一 JSONL IPC 协议的 search 引擎"""

    IGNORED_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache"}

    def __init__(self):
        self.methods = {
            "search": self.search,
            "count": self.count,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "search",
            "version": "1.1.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": [
                "recursive_regex_search", "file_filtering",
                "glob_include_exclude", "ignore_case",
                "context_lines", "per_file_count",
            ],
        }

    @staticmethod
    def _glob_filtered(rel_path: str, include_globs, exclude_globs) -> bool:
        """相对路径是否被 glob 过滤规则放行 (True=放行)。"""
        if exclude_globs and any(fnmatch(rel_path, g) for g in exclude_globs):
            return False
        if include_globs and not any(fnmatch(rel_path, g) for g in include_globs):
            return False
        return True

    def _is_binary(self, filepath: str) -> bool:
        """检查是否二进制文件"""
        try:
            with open(filepath, "rb") as f:
                chunk = f.read(1024)
                return b"\x00" in chunk
        except Exception:
            return True

    def _compile(self, pattern: str, ignore_case: bool):
        """编译正则; 返回 (regex, error)。"""
        if not pattern:
            return None, "No pattern provided"
        try:
            flags = re.IGNORECASE if ignore_case else 0
            return re.compile(pattern, flags), None
        except re.error as e:
            return None, f"Invalid regex: {e}"

    def search(self, params):
        """递归正则检索
        可选参数:
          include_globs / exclude_globs  相对路径 glob 过滤 (如 "*.py")
          ignore_case                    忽略大小写
          context                        每条命中附带的前后上下文行数 (0-10)
        命中达到 max_results 时返回 truncated=true。
        """
        root = params.get("root", ".")
        max_results = int(params.get("max_results", 100))
        include_globs = params.get("include_globs") or []
        exclude_globs = params.get("exclude_globs") or []
        context = max(0, min(int(params.get("context", 0)), _MAX_CONTEXT))

        regex, err = self._compile(params.get("pattern", ""), bool(params.get("ignore_case", False)))
        if err:
            return {"results": [], "count": 0, "error": err}

        results = []
        truncated = False
        for dirpath, dirnames, filenames in os.walk(root):
            # 过滤忽略目录
            dirnames[:] = [d for d in dirnames if d not in self.IGNORED_DIRS]

            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                rel = os.path.relpath(filepath, root).replace("\\", "/")
                if not self._glob_filtered(rel, include_globs, exclude_globs):
                    continue
                # 跳过二进制
                if self._is_binary(filepath):
                    continue

                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except Exception:
                    continue

                stop = False
                for i, line in enumerate(lines):
                    if not regex.search(line):
                        continue
                    hit = {
                        "file": filepath,
                        "line": i + 1,
                        "content": line.rstrip()[:500],
                    }
                    if context > 0:
                        lo = max(0, i - context)
                        hi = min(len(lines), i + context + 1)
                        hit["context"] = [
                            {"line": n + 1, "content": lines[n].rstrip()[:500]}
                            for n in range(lo, hi)
                        ]
                    results.append(hit)
                    if len(results) >= max_results:
                        truncated = True
                        stop = True
                        break
                if stop:
                    break
            if truncated:
                break

        resp = {"results": results, "count": len(results)}
        if truncated:
            resp["truncated"] = True
        return resp

    def count(self, params):
        """按文件统计匹配行数 (不返回内容行, 用于快速概览代码分布)"""
        root = params.get("root", ".")
        max_files = int(params.get("max_files", 200))
        include_globs = params.get("include_globs") or []
        exclude_globs = params.get("exclude_globs") or []

        regex, err = self._compile(params.get("pattern", ""), bool(params.get("ignore_case", False)))
        if err:
            return {"files": [], "total_matches": 0, "file_count": 0, "error": err}

        files_out = []
        total = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.IGNORED_DIRS]
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                rel = os.path.relpath(filepath, root).replace("\\", "/")
                if not self._glob_filtered(rel, include_globs, exclude_globs):
                    continue
                if self._is_binary(filepath):
                    continue
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        n = sum(1 for line in f if regex.search(line))
                except Exception:
                    continue
                if n:
                    files_out.append({"file": filepath, "matches": n})
                    total += n
                    if len(files_out) >= max_files:
                        break
            if len(files_out) >= max_files:
                break

        return {"files": files_out, "total_matches": total, "file_count": len(files_out)}

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
