"""纯 Python 实现 ansi 引擎 (替代 ext/c/ansi.c)
终端转义解析 / 剥离 / 渲染
"""
import json
import sys
import re


class AnsiEngine:
    """统一 JSONL IPC 协议的 ansi 引擎"""

    ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*\x1b")

    def __init__(self):
        self.methods = {
            "strip": self.strip,
            "render": self.render,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "ansi",
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["strip_ansi", "render_ansi"],
        }

    def strip(self, params):
        """剥离 ANSI 转义序列"""
        text = params.get("text", "")
        cleaned = self.ANSI_RE.sub("", text)
        return {"text": cleaned, "stripped_chars": len(text) - len(cleaned)}

    def render(self, params):
        """渲染带颜色的文本 (简化为原样返回)"""
        text = params.get("text", "")
        return {"text": text, "rendered": True}

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
    AnsiEngine().run()
