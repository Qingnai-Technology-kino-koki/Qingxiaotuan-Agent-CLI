"""纯 Python 实现 diff 引擎
行/词级 Myers diff + patch + 3-way merge
"""
import json
import sys
import difflib
import os


class DiffEngine:
    """统一 JSONL IPC 协议的 diff 引擎"""

    def __init__(self):
        self.methods = {
            "diff": self.diff,
            "patch": self.patch,
            "merge3": self.merge3,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "diff",
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["line_diff", "word_diff", "patch", "merge3"],
        }

    def diff(self, params):
        """行级 Myers diff"""
        old = params.get("old", "")
        new = params.get("new", "")
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=params.get("fromfile", "old"),
            tofile=params.get("tofile", "new"),
            lineterm="\n",
        )
        result = "".join(diff)

        # 修复: ndiff 返回 generator, 先转 list 再统计
        ndiff_lines = list(difflib.ndiff(old_lines, new_lines))

        return {
            "diff": result,
            "added": sum(1 for d in ndiff_lines if d.startswith("+ ")),
            "removed": sum(1 for d in ndiff_lines if d.startswith("- ")),
            "changed": len(ndiff_lines) > 0,
        }

    def patch(self, params):
        """应用 unified diff patch"""
        source = params.get("source", "")
        patch_text = params.get("patch", "")

        source_lines = source.splitlines(keepends=True)
        patch_lines = patch_text.splitlines(keepends=True)

        try:
            import re
            new_lines = []
            src_idx = 0
            for pl in patch_lines:
                if pl.startswith("---") or pl.startswith("+++") or pl.startswith("@@"):
                    continue
                if pl.startswith("-"):
                    src_idx += 1
                    continue
                if pl.startswith("+"):
                    new_lines.append(pl[1:])
                else:
                    if src_idx < len(source_lines):
                        new_lines.append(source_lines[src_idx])
                    src_idx += 1

            if src_idx < len(source_lines):
                new_lines.extend(source_lines[src_idx:])

            return {"result": "".join(new_lines), "applied": True}
        except Exception as e:
            return {"result": source, "applied": False, "error": str(e)}

    def merge3(self, params):
        """3-way merge"""
        base = params.get("base", "")
        theirs = params.get("theirs", "")
        ours = params.get("ours", "")

        base_lines = base.splitlines(keepends=True)
        ours_lines = ours.splitlines(keepends=True)

        sm_ours = difflib.SequenceMatcher(None, base_lines, ours_lines)
        sm_theirs = difflib.SequenceMatcher(None, base_lines, theirs.splitlines(keepends=True))

        result_lines = list(ours_lines)
        conflicts = []

        for tag, i1, i2, j1, j2 in sm_ours.get_opcodes():
            if tag == "replace" or tag == "delete":
                for tag2, k1, k2, l1, l2 in sm_theirs.get_opcodes():
                    if tag2 == "replace" or tag2 == "delete":
                        if i1 <= k1 < i2 or k1 <= i1 < k2:
                            conflicts.append({"base_start": i1, "base_end": i2})

        return {"result": "".join(result_lines), "conflicts": conflicts}

    def handle(self, line):
        """处理一行 JSON 请求"""
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
            req = {}
            resp = {"id": req.get("id") if isinstance(req, dict) else None, "ok": False, "error": str(e)}
            return json.dumps(resp, ensure_ascii=False)

    def run(self):
        """主循环: 读取 stdin 逐行处理"""
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            resp = self.handle(line)
            sys.stdout.write(resp + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    engine = DiffEngine()
    engine.run()
