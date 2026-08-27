"""纯 Python 实现 ansi 引擎
终端转义解析 / 剥离 / SGR 渲染 / CJK 感知显示宽度 (计宽/截断/补齐)
"""
import json
import sys
import re
import unicodedata


# 终端调色板: 颜色名 -> SGR 前景码 (30-37 标准, 90-97 高亮)
_PALETTE = {
    "black": 30, "red": 31, "green": 32, "yellow": 33,
    "blue": 34, "magenta": 35, "cyan": 36, "white": 37,
    "bright_black": 90, "bright_red": 91, "bright_green": 92,
    "bright_yellow": 93, "bright_blue": 94, "bright_magenta": 95,
    "bright_cyan": 96, "bright_white": 97,
}

_RESET = "\x1b[0m"


def _char_width(ch: str) -> int:
    """单字符显示宽度: 组合字符记 0, CJK/全角记 2, 其余记 1。"""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _display_width(text: str, strip_re: "re.Pattern[str]") -> int:
    """剥离 ANSI 转义后按终端显示列数计宽。"""
    return sum(_char_width(ch) for ch in strip_re.sub("", text))


class AnsiEngine:
    """统一 JSONL IPC 协议的 ansi 引擎"""

    ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*\x1b")

    def __init__(self):
        self.methods = {
            "strip": self.strip,
            "render": self.render,
            "width": self.width,
            "truncate": self.truncate,
            "pad": self.pad,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "ansi",
            "version": "1.1.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": [
                "strip_ansi", "sgr_render",
                "display_width", "cjk_aware",
                "truncate_by_width", "pad_by_width",
            ],
        }

    def strip(self, params):
        """剥离 ANSI 转义序列"""
        text = params.get("text", "")
        cleaned = self.ANSI_RE.sub("", text)
        return {"text": cleaned, "stripped_chars": len(text) - len(cleaned)}

    def render(self, params):
        """渲染 SGR 样式文本。

        fg 支持 black/red/green/yellow/blue/magenta/cyan/white 及 bright_* 高亮
        变体; bold 加粗。未提供任何样式时原样返回 (与旧版行为一致)。
        """
        text = params.get("text", "")
        fg = params.get("fg")
        codes = []
        if params.get("bold"):
            codes.append("1")
        if isinstance(fg, str):
            code = _PALETTE.get(fg.lower())
            if code is not None:
                codes.append(str(code))
        if not codes:
            return {"text": text, "rendered": True}
        return {"text": f"\x1b[{';'.join(codes)}m{text}{_RESET}", "rendered": True}

    # ------------------------------------------------------------- 显示宽度

    def width(self, params):
        """计算文本的终端显示宽度 (剥离转义序列, CJK 记 2 列)。"""
        text = params.get("text", "")
        return {"text": text, "width": _display_width(text, self.ANSI_RE)}

    def truncate(self, params):
        """按显示宽度截断文本 (先剥离转义序列)。

        截断发生时末尾追加 marker 并为 marker 预留宽度; max_width 连 marker
        都容不下时返回空串。
        """
        text = params.get("text", "")
        try:
            max_width = int(params.get("max_width", 0))
        except (TypeError, ValueError):
            max_width = 0
        marker = str(params.get("marker", "…"))
        plain = self.ANSI_RE.sub("", text)
        total = _display_width(plain, self.ANSI_RE)
        if total <= max_width:
            return {"text": plain, "truncated": False, "width": total}
        budget = max_width - _display_width(marker, self.ANSI_RE)
        out, w = [], 0
        for ch in plain:
            cw = _char_width(ch)
            if w + cw > budget:
                break
            out.append(ch)
            w += cw
        result = "" if budget < 0 else "".join(out) + marker
        return {"text": result, "truncated": True, "width": _display_width(result, self.ANSI_RE)}

    def pad(self, params):
        """把文本用空格补齐到指定显示宽度 (超宽则原样返回)。

        align: left (默认, 右补空格) / right (左补) / center (两侧均分)。
        """
        text = params.get("text", "")
        try:
            width = int(params.get("width", 0))
        except (TypeError, ValueError):
            width = 0
        align = str(params.get("align", "left")).lower()
        w = _display_width(text, self.ANSI_RE)
        fill = width - w
        if fill <= 0:
            return {"text": text, "padded": False, "content_width": w}
        if align == "right":
            out = " " * fill + text
        elif align == "center":
            left = fill // 2
            out = " " * left + text + " " * (fill - left)
        else:
            out = text + " " * fill
        return {"text": out, "padded": True, "content_width": w}

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
