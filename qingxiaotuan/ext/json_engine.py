"""纯 Python 实现 json 引擎 (替代 ext/c/json.c)
RFC 6901 Pointer 精确取值 / 逐路径 diff / 深合并
"""
import json
import sys


def _pointer_get(obj, pointer: str):
    """RFC 6901 JSON Pointer 取值"""
    if not pointer:
        return obj
    parts = pointer.strip("/").split("/") if pointer != "/" else []
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(obj, dict):
            obj = obj[part]
        elif isinstance(obj, list):
            obj = obj[int(part)]
        else:
            raise KeyError(f"Cannot traverse {part}")
    return obj


def _deep_diff(base, overlay, path=""):
    """逐路径 diff"""
    diffs = []
    if isinstance(base, dict) and isinstance(overlay, dict):
        for k in set(base.keys()) | set(overlay.keys()):
            new_path = f"{path}/{k}" if path else f"/{k}"
            if k not in base:
                diffs.append({"path": new_path, "old": None, "new": overlay[k], "op": "add"})
            elif k not in overlay:
                diffs.append({"path": new_path, "old": base[k], "new": None, "op": "remove"})
            elif base[k] != overlay[k]:
                diffs.append({"path": new_path, "old": base[k], "new": overlay[k], "op": "replace"})
    elif isinstance(base, list) and isinstance(overlay, list):
        for i in range(max(len(base), len(overlay))):
            new_path = f"{path}/{i}"
            if i >= len(base):
                diffs.append({"path": new_path, "old": None, "new": overlay[i], "op": "add"})
            elif i >= len(overlay):
                diffs.append({"path": new_path, "old": base[i], "new": None, "op": "remove"})
            elif base[i] != overlay[i]:
                diffs.append({"path": new_path, "old": base[i], "new": overlay[i], "op": "replace"})
    else:
        if base != overlay:
            diffs.append({"path": path or "/", "old": base, "new": overlay, "op": "replace"})
    return diffs


def _deep_merge(base, overlay):
    """深合并 (overlay 覆盖 base)"""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for k, v in overlay.items():
            if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                merged[k] = _deep_merge(merged[k], v)
            else:
                merged[k] = v
        return merged
    return overlay


class JsonEngine:
    """统一 JSONL IPC 协议的 json 引擎"""

    def __init__(self):
        self.methods = {
            "pointer_get": self.pointer_get,
            "diff": self.diff,
            "merge": self.merge,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "json",
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["rfc6901_pointer", "deep_diff", "deep_merge"],
        }

    def pointer_get(self, params):
        """RFC 6901 Pointer 取值 (未命中时抛异常 -> 上层转 ok:false)"""
        doc = params.get("doc", {})
        pointer = params.get("pointer", "")
        result = _pointer_get(doc, pointer)
        return {"value": result, "found": True}

    def diff(self, params):
        """逐路径 diff"""
        base = params.get("base", {})
        overlay = params.get("overlay", {})
        diffs = _deep_diff(base, overlay)
        return {"changes": diffs, "count": len(diffs)}

    def merge(self, params):
        """深合并"""
        base = params.get("base", {})
        overlay = params.get("overlay", {})
        merged = _deep_merge(base, overlay)
        return {"merged": merged}

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
    JsonEngine().run()
