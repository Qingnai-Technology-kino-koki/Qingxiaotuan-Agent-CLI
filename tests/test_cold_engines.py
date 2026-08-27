"""冷门引擎增强功能的单元测试。

直接实例化 qingxiaotuan/ext 下的引擎 (不经 IPC 子进程), 覆盖:
- search: glob 过滤 / ignore_case / context / truncated / count
- index: 缩进 def、async、Go/Rust/Java 符号提取 + 行号 + kind
- notify: Windows 回退链 / beep / dry_run / 诊断信息
- crypto: hmac_sign/hmac_verify / random_token / iterations 加固 / 解码容错
"""

import base64
import json

import pytest

from qingxiaotuan.ext.crypto_engine import CryptoEngine
from qingxiaotuan.ext.index_engine import IndexEngine
from qingxiaotuan.ext.notify_engine import NotifyEngine
from qingxiaotuan.ext.search_engine import SearchEngine


# ---------------------------------------------------------------- search

@pytest.fixture()
def search_tree(tmp_path):
    """构造检索样本树: 两个命中文件 + 一个被忽略目录中的命中文件。"""
    (tmp_path / "a.py").write_text("x = 1  # TODO fix\ny = 2\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("todo list\ndone\n", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "junk.py").write_text("TODO hidden\n", encoding="utf-8")
    return tmp_path


def test_search_ignore_case(search_tree):
    eng = SearchEngine()
    upper = eng.search({"root": str(search_tree), "pattern": "TODO"})
    assert upper["count"] == 1  # 仅 a.py 的大写命中
    both = eng.search({"root": str(search_tree), "pattern": "todo", "ignore_case": True})
    assert both["count"] == 2
    assert not both["results"][0]["content"].startswith("TODO hidden")  # 忽略目录不出现


def test_search_globs(search_tree):
    eng = SearchEngine()
    only_py = eng.search({"root": str(search_tree), "pattern": "[Tt][Oo][Dd][Oo]",
                          "include_globs": ["*.py"]})
    assert {r["file"].replace("\\", "/").endswith("a.py") for r in only_py["results"]} == {True}
    no_txt = eng.search({"root": str(search_tree), "pattern": "[Tt][Oo][Dd][Oo]",
                         "exclude_globs": ["*.txt"]})
    assert all(not r["file"].endswith("b.txt") for r in no_txt["results"])
    assert no_txt["count"] == 1


def test_search_context_lines(search_tree):
    eng = SearchEngine()
    res = eng.search({"root": str(search_tree), "pattern": "todo",
                      "ignore_case": True, "context": 1})
    hit_b = next(r for r in res["results"] if r["file"].endswith("b.txt"))
    # b.txt 第 1 行命中, 上下文应含第 2 行 done
    ctx_lines = [c["line"] for c in hit_b["context"]]
    assert 1 in ctx_lines and 2 in ctx_lines
    assert any(c["content"] == "done" for c in hit_b["context"])


def test_search_truncated_flag(search_tree):
    eng = SearchEngine()
    res = eng.search({"root": str(search_tree), "pattern": "todo",
                      "ignore_case": True, "max_results": 1})
    assert res["count"] == 1 and res.get("truncated") is True
    full = eng.search({"root": str(search_tree), "pattern": "todo", "ignore_case": True})
    assert full["count"] == 2 and "truncated" not in full


def test_search_invalid_regex(search_tree):
    eng = SearchEngine()
    res = eng.search({"root": str(search_tree), "pattern": "([bad"})
    assert res["count"] == 0 and "Invalid regex" in res["error"]


def test_count_per_file(search_tree):
    eng = SearchEngine()
    res = eng.count({"root": str(search_tree), "pattern": "todo", "ignore_case": True})
    assert res["total_matches"] == 2 and res["file_count"] == 2
    per_file = {f["file"].split("/")[-1].split("\\")[-1]: f["matches"] for f in res["files"]}
    assert per_file == {"a.py": 1, "b.txt": 1}
    py_only = eng.count({"root": str(search_tree), "pattern": "todo",
                         "ignore_case": True, "include_globs": ["*.py"]})
    assert py_only["file_count"] == 1


# ---------------------------------------------------------------- index

PY_SRC = (
    "class Foo:\n"          # 1 class
    "    def bar(self):\n"  # 2 缩进 def
    "        pass\n"
    "\n"
    "async def baz():\n"    # 5 async def
    "    pass\n"
    "\n"
    "def top(): pass\n"     # 8 顶层 def
)


def test_index_python_indented_async_line_numbers(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(PY_SRC, encoding="utf-8")
    out = IndexEngine().index({"paths": [str(src)]})
    item = out["indexed"][0]
    # 向后兼容: symbols 仍是名字列表
    assert set(item["symbols"]) >= {"Foo", "bar", "baz", "top"}
    details = {(d["name"], d["kind"]): d["line"] for d in item["symbol_details"]}
    assert details[("Foo", "class")] == 1
    assert details[("bar", "def")] == 2      # 旧版正则漏掉缩进 def
    assert details[("baz", "def")] == 5      # 旧版正则漏掉 async def
    assert details[("top", "def")] == 8


def test_index_go_rust_java(tmp_path):
    go = tmp_path / "s.go"
    go.write_text("package main\nfunc main() {}\nfunc (s *Srv) Start() {}\ntype Server struct {\n", encoding="utf-8")
    rs = tmp_path / "l.rs"
    rs.write_text("fn main() {}\nstruct Config;\ntrait Run {\n    fn go(&self);\n}\n", encoding="utf-8")
    ja = tmp_path / "A.java"
    ja.write_text("public class App {}\ninterface Repo {}\n", encoding="utf-8")
    out = IndexEngine().index({"paths": [str(go), str(rs), str(ja)]})
    by_file = {i["path"]: i for i in out["indexed"]}
    go_syms = by_file[str(go)]
    assert set(go_syms["symbols"]) >= {"main", "Start", "Server"}
    kinds = {(d["name"], d["kind"]) for d in go_syms["symbol_details"]}
    assert ("Server", "type") in kinds and ("Start", "func") in kinds
    assert set(by_file[str(rs)]["symbols"]) >= {"main", "Config", "Run", "go"}
    assert set(by_file[str(ja)]["symbols"]) >= {"App", "Repo"}


def test_index_query_backward_compat(tmp_path):
    src = tmp_path / "q.py"
    src.write_text("def hello():\n    return 1\n", encoding="utf-8")
    eng = IndexEngine()
    built = eng.index({"paths": [str(src)]})
    hits = eng.search({"query": "hello", "index": built["indexed"],
                       "path": "q.py", "lang": "python"})
    assert hits["count"] == 1


# ---------------------------------------------------------------- notify

def test_notify_meta_lists_new_methods():
    meta = NotifyEngine().list_methods()
    assert "beep" in meta["methods"]
    assert "windows_fallback" in meta["capabilities"]
    assert meta["version"].startswith("1.1")


def test_notify_dry_run_reports_backend_without_sending(monkeypatch):
    eng = NotifyEngine()
    called = []
    monkeypatch.setattr("qingxiaotuan.ext.notify_engine._run_cmd",
                        lambda *a, **k: called.append(a) or (0, ""))
    res = eng.notify({"title": "t", "message": "m", "dry_run": True})
    assert res["dry_run"] is True and res["sent"] is False
    assert res["backend"] and not called  # 未实际执行任何命令


def test_notify_windows_fallback_chain(monkeypatch):
    import qingxiaotuan.ext.notify_engine as ne

    eng = ne.NotifyEngine()
    scripted = []

    def fake_run(cmd, timeout=5):
        return scripted.pop(0) if scripted else (1, "no-script")

    monkeypatch.setattr(ne, "_run_cmd", fake_run)

    # 场景一: toast 失败 -> 气球通知成功
    scripted = [(1, "boom"), (0, "")]
    ok = eng._send("标题", "内容", "Windows")
    assert ok["sent"] is True and ok["backend"] == "balloon"
    assert ok["fallback_used"] is True and ok["first_error"] == "boom"

    # 场景二: 两条路都失败 -> sent=False 且带诊断
    scripted = [(1, "toast-down"), (1, "balloon-down")]
    bad = eng._send("标题", "内容", "Windows")
    assert bad["sent"] is False and "balloon-down" in bad["error"]


def test_notify_beep_dry_run_and_handle_envelope():
    eng = NotifyEngine()
    res = eng.beep({"dry_run": True})
    assert res["played"] is False and res["way"]
    resp = json.loads(eng.handle(json.dumps(
        {"id": 7, "method": "beep", "params": {"dry_run": True}})))
    assert resp["id"] == 7 and resp["ok"] is True
    bad = json.loads(eng.handle(json.dumps({"id": 8, "method": "nope"})))
    assert bad["ok"] is False


# ---------------------------------------------------------------- crypto

def test_crypto_hmac_roundtrip_and_tamper():
    eng = CryptoEngine()
    signed = eng.hmac_sign({"data": "payload", "passphrase": "pw"})
    mac = signed["mac_b64"]
    assert eng.hmac_verify({"data": "payload", "passphrase": "pw", "mac_b64": mac})["valid"] is True
    assert eng.hmac_verify({"data": "tampered", "passphrase": "pw", "mac_b64": mac})["valid"] is False


def test_crypto_hmac_with_derived_key_b64():
    eng = CryptoEngine()
    key = eng.derive_key({"passphrase": "pw"})["key_b64"]
    mac = eng.hmac_sign({"data": "x", "key_b64": key})["mac_b64"]
    assert eng.hmac_verify({"data": "x", "key_b64": key, "mac_b64": mac})["valid"] is True


def test_crypto_random_token():
    eng = CryptoEngine()
    t1 = eng.random_token({})
    t2 = eng.random_token({"length": 16})
    assert t1["bytes"] == 32 and t2["bytes"] == 16
    assert t1["token"] != t2["token"]
    assert "+" not in t1["token"] and "/" not in t1["token"]  # URL 安全
    with pytest.raises(ValueError):
        eng.random_token({"length": 0})


def test_crypto_iterations_hardening():
    eng = CryptoEngine()
    for params in ({"iterations": 0}, {"iterations": 500}, {"iterations": "many"}):
        with pytest.raises(ValueError):
            eng.seal({"plaintext": "x", "passphrase": "pw", **params})
        with pytest.raises(ValueError):
            eng.derive_key({"passphrase": "pw", **params})


def test_crypto_open_invalid_utf8_friendly_error():
    """确定性构造必然非法的 UTF-8 明文: 用引擎内部函数反向生成密文。"""
    from qingxiaotuan.ext.crypto_engine import _derive_key, _xor_stream

    eng = CryptoEngine()
    salt = b"0123456789abcdef"
    iv = b"0123456789abcdef"
    key = _derive_key("pw", salt, 100000)
    # 目标明文 0xFF 永远不是合法 UTF-8; 密文 = 目标 XOR keystream (对称)
    ciphertext = _xor_stream(b"\xff\xff\xff", key)
    with pytest.raises(ValueError) as excinfo:
        eng.open({"passphrase": "pw",
                  "salt_b64": base64.b64encode(salt).decode(),
                  "iv_b64": base64.b64encode(iv).decode(),
                  "ciphertext_b64": base64.b64encode(ciphertext).decode()})
    assert "UTF-8" in str(excinfo.value)


def test_crypto_seal_open_roundtrip_regression():
    eng = CryptoEngine()
    sealed = eng.seal({"plaintext": "青小团秘密", "passphrase": "pw"})
    opened = eng.open({"passphrase": "pw", "salt_b64": sealed["salt_b64"],
                       "iv_b64": sealed["iv_b64"],
                       "ciphertext_b64": sealed["ciphertext_b64"],
                       "mac_b64": sealed["mac_b64"],
                       "iterations": sealed["iterations"]})
    assert opened["plaintext"] == "青小团秘密"
    wrong = eng.handle(json.dumps({
        "id": 1, "method": "open",
        "params": {"passphrase": "bad", "salt_b64": sealed["salt_b64"],
                   "iv_b64": sealed["iv_b64"],
                   "ciphertext_b64": sealed["ciphertext_b64"],
                   "mac_b64": sealed["mac_b64"]}}))
    body = json.loads(wrong)
    assert body["ok"] is False and "tag mismatch" in body["error"]


# ---------------------------------------------------------------- ansi

def test_ansi_width_cjk_and_stripped():
    from qingxiaotuan.ext.ansi_engine import AnsiEngine

    eng = AnsiEngine()
    assert eng.width({"text": "abc"})["width"] == 3
    assert eng.width({"text": "青小团"})["width"] == 6          # CJK 记 2 列
    colored = eng.width({"text": "\x1b[31m青小团\x1b[0m"})["width"]
    stripped = eng.width({"text": "青小团"})["width"]
    assert colored == stripped == 6                             # 转义序列不占宽


def test_ansi_truncate_cjk_boundary_and_marker_budget():
    from qingxiaotuan.ext.ansi_engine import AnsiEngine

    eng = AnsiEngine()
    # "青小团ABC" 宽 9; 截到 7 (marker "…" 宽 1, 预算 6) → 恰好放下 "青小团"
    res = eng.truncate({"text": "青小团ABC", "max_width": 7})
    assert res["truncated"] is True
    assert res["text"] == "青小团…"
    assert res["width"] <= 7                                    # 含 marker 不超预算
    # 无需截断时原样返回且 truncated=False
    keep = eng.truncate({"text": "青小团", "max_width": 10})
    assert keep == {"text": "青小团", "truncated": False, "width": 6}
    # 预算恰为 0 (max_width 刚好容纳 marker): 只剩 marker 本身
    tiny = eng.truncate({"text": "青小团", "max_width": 1})
    assert tiny == {"text": "…", "truncated": True, "width": 1}
    # max_width 连 marker 都容不下 → 空串
    none = eng.truncate({"text": "青小团", "max_width": 1, "marker": "..."})
    assert none == {"text": "", "truncated": True, "width": 0}
    # 自定义 marker
    dash = eng.truncate({"text": "abcdef", "max_width": 5, "marker": ".."})
    assert dash["text"] == "abc.."


def test_ansi_pad_aligns_by_display_width():
    from qingxiaotuan.ext.ansi_engine import AnsiEngine

    eng = AnsiEngine()
    left = eng.pad({"text": "ab", "width": 5})
    assert left["text"] == "ab   " and left["padded"] is True
    right = eng.pad({"text": "青", "width": 5, "align": "right"})
    assert right["text"] == "   青" and right["content_width"] == 2
    center = eng.pad({"text": "abc", "width": 8, "align": "center"})
    assert center["text"] == "  abc   "                        # 左 2 右 3
    # 已超宽则原样返回
    over = eng.pad({"text": "青小团ABC", "width": 4})
    assert over["padded"] is False and over["text"] == "青小团ABC"


def test_ansi_render_sgr_and_backward_compat():
    from qingxiaotuan.ext.ansi_engine import AnsiEngine

    eng = AnsiEngine()
    out = eng.render({"text": "err", "fg": "red", "bold": True})["text"]
    assert out.startswith("\x1b[1;31m") and out.endswith("\x1b[0m")
    bright = eng.render({"text": "x", "fg": "bright_cyan"})["text"]
    assert bright.startswith("\x1b[96m")
    # 未提供样式: 与旧版一致原样返回
    plain = eng.render({"text": "\x1b[32malready\x1b[0m"})
    assert plain == {"text": "\x1b[32malready\x1b[0m", "rendered": True}


def test_ansi_handle_envelope_new_methods():
    from qingxiaotuan.ext.ansi_engine import AnsiEngine

    eng = AnsiEngine()
    resp = json.loads(eng.handle(json.dumps(
        {"id": 1, "method": "width", "params": {"text": "青"}})))
    assert resp["ok"] is True and resp["result"]["width"] == 2
    resp = json.loads(eng.handle(json.dumps(
        {"id": 2, "method": "pad", "params": {"text": "a", "width": 3}})))
    assert resp["result"]["text"] == "a  "
    meta = eng.list_methods()
    assert meta["version"].startswith("1.1") and "display_width" in meta["capabilities"]
