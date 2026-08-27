"""web_fetch 加固: 异常隔离 + gzip + 中文编码 + 错误 URL + SSRF 防护。"""
import gzip
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from qingxiaotuan.tools.web import web_fetch, _check_ssrf
from qingxiaotuan.tools.base import ToolContext


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/gzip":
            body = gzip.compress("<html><body><h1>青小团CLI</h1>中文网页测试</body></html>".encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/gbk":
            body = "<html><body>国标编码页面</body></html>".encode("gbk")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=gbk")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/404":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"not found")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>hello world</p>".encode("utf-8"))


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _ctx():
    # ToolContext.config() 走 kernel.get("config").get(dotted, default)
    # 注意: ToolContext 用类级注解, 必须按位置传参 (无 __init__)
    class _Cfg:
        def get(self, dotted, default=None):
            # 本地测试服务器需要放行 loopback (SSRF 防护默认拦截 127.0.0.1)
            if dotted == "tools.web.allow_loopback":
                return True
            return default
    class _Kernel:
        def get(self, name):
            return _Cfg() if name == "config" else None
    tc = ToolContext.__new__(ToolContext)
    tc.kernel = _Kernel()
    tc.workspace = "."
    tc.confirm = lambda n: True
    tc.yolo = False
    tc.on_auto_approve = None
    return tc


def test_web_fetch_plain(server):
    out = web_fetch(_ctx(), server + "/")
    assert "hello world" in out


def test_web_fetch_gzip(server):
    out = web_fetch(_ctx(), server + "/gzip")
    assert "青小团CLI" in out
    assert "中文网页测试" in out


def test_web_fetch_gbk_charset(server):
    out = web_fetch(_ctx(), server + "/gbk")
    assert "国标编码页面" in out


def test_web_fetch_http_error(server):
    out = web_fetch(_ctx(), server + "/404")
    assert out.startswith("[web_fetch] HTTP 错误 404")


def test_web_fetch_invalid_url():
    out = web_fetch(_ctx(), "ftp://example.com/x")
    assert out.startswith("[web_fetch] 无效 URL")


def test_web_fetch_unreachable():
    out = web_fetch(_ctx(), "http://127.0.0.1:1/nope")
    assert out.startswith("[web_fetch]")


# ---------------------------------------------------------------- SSRF 防护

def test_ssrf_blocks_loopback_by_default():
    assert _check_ssrf("http://127.0.0.1:8080/") is not None
    assert _check_ssrf("http://[::1]:8080/") is not None


def test_ssrf_allows_loopback_when_configured():
    assert _check_ssrf("http://127.0.0.1:8080/", allow_loopback=True) is None
    assert _check_ssrf("http://[::1]:8080/", allow_loopback=True) is None


def test_ssrf_blocks_private_and_linklocal():
    for url in ("http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.5/",
                "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/",
                "http://100.64.0.1/"):
        assert _check_ssrf(url) is not None, url


def test_ssrf_blocks_private_even_when_loopback_allowed():
    # allow_loopback 只放行 127.0.0.0/8 与 ::1, 内网段仍拦截
    assert _check_ssrf("http://10.0.0.1/", allow_loopback=True) is not None
    assert _check_ssrf("http://169.254.169.254/", allow_loopback=True) is not None


def test_ssrf_allows_public_url():
    assert _check_ssrf("http://example.com/") is None
    assert _check_ssrf("https://www.python.org/") is None


def test_ssrf_handles_malformed_url():
    assert _check_ssrf("not-a-url") is not None


def test_web_fetch_blocks_loopback_when_not_allowed():
    class _StrictCfg:
        def get(self, dotted, default=None):
            return default
    class _Kernel:
        def get(self, name):
            return _StrictCfg() if name == "config" else None
    tc = ToolContext.__new__(ToolContext)
    tc.kernel = _Kernel()
    tc.workspace = "."
    tc.confirm = lambda n: True
    tc.yolo = False
    tc.on_auto_approve = None
    out = web_fetch(tc, "http://127.0.0.1:8080/")
    assert "已拦截" in out
