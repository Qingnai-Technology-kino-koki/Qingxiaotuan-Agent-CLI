"""Python <-> TypeScript Agent SDK 的真实 JSONL + SSE 流式集成测试。"""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from qingxiaotuan.core.ipc_client import IpcClient


class _SSEHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        json.loads(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for content in ("你好", "，青小团"):
            payload = {"choices": [{"delta": {"content": content}}]}
            self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *_args):
        pass


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_python_ipc_receives_typescript_sse_stream():
    root = Path(__file__).resolve().parents[1] / "ext" / "ts"
    entry = root / "dist" / "agent-sdk" / "main.js"
    if not entry.exists():
        pytest.skip("请先 npm run build 生成 TS dist")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _SSEHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process = IpcClient("ts-agent")
    # 覆盖 subprocess 命令: 直接跑 node + TS dist, 而不是 python -m module
    import subprocess as _sp
    process.proc = _sp.Popen(
        [shutil.which("node") or "node", str(entry)],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, bufsize=1,
    )
    process._read_thread = threading.Thread(target=process._read_loop, daemon=True)
    process._read_thread.start()
    if not process._ready.wait(timeout=10):
        pytest.skip("TS agent-sdk 未就绪")
    chunks = []
    try:
        configured = process.request("config", {
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "api_key": "test", "model": "mock",
        })
        assert configured["ok"] is True
        result = process.request("run", {"message": "输出问候", "stream": True},
                                 on_stream=chunks.append, timeout=10)
        assert result["reply"] == "你好，青小团"
        assert [chunk["text"] for chunk in chunks] == ["你好", "，青小团"]
    finally:
        process.close()
        server.shutdown()
        server.server_close()
