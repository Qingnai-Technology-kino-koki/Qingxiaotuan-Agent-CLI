import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ------------------------------------------------------------------ 进程级超时保护
# 如果操作系统支持 SIGALRM (POSIX), 注册一个全局看门狗防止单个测试无限挂起。
# Windows 不支持 SIGALRM, 依赖 pytest-timeout 的 thread 方法作为后备。
_GLOBAL_TIMEOUT = int(os.environ.get("PYTEST_GLOBAL_TIMEOUT", "300"))
if hasattr(signal, "SIGALRM"):
    def _global_watchdog(signum, frame):
        pytest.exit(
            f"[FATAL] 全局超时 ({_GLOBAL_TIMEOUT}s) 触发, 强制退出 pytest。\n"
            "可能存在挂起的测试。请检查: pytest --timeout=120 --timeout-method=process",
            returncode=1,
        )
    signal.signal(signal.SIGALRM, _global_watchdog)
    signal.alarm(_GLOBAL_TIMEOUT)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(session, exitstatus):
        """测试会话结束时取消全局超时闹钟。"""
        signal.alarm(0)

# ------------------------------------------------------------------ 依赖健康检查
# 缺少 httpx/rich/yaml 会导致 24+ 个测试模块 collection error,
# 这里统一拦截并给出友好提示。
_missing = []
for _mod in ("httpx", "rich", "yaml"):
    try:
        __import__(_mod)
    except (ImportError, TypeError):
        _missing.append(_mod)
if _missing:
    pytest.exit(
        f"缺少测试依赖: {', '.join(_missing)}\n"
        "请先激活虚拟环境 (Windows: .venv/Scripts/activate) 再运行测试,\n"
        "或执行: pip install httpx rich pyyaml",
        returncode=1,
    )

from qingxiaotuan.core.ipc_client import close_all_managers


@pytest.fixture(autouse=True)
def _cleanup_ipc_managers():
    """每个测试后关闭所有长驻外部引擎进程, 避免跨测试 IPC 句柄泄漏 (Windows 敏感)。"""
    yield
    try:
        close_all_managers()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _refresh_global_alarm():
    """每个测试开始时刷新全局超时闹钟, 防止前一个测试的残留 alarm 干扰当前测试。"""
    if hasattr(signal, "SIGALRM"):
        signal.alarm(_GLOBAL_TIMEOUT)
    yield
    if hasattr(signal, "SIGALRM"):
        signal.alarm(_GLOBAL_TIMEOUT)  # 刷新为下一次测试重置


@pytest.fixture()
def qxt_home(tmp_path, monkeypatch):
    """每个测试使用独立的 QXT_HOME, 不污染真实用户目录。"""
    home = tmp_path / ".qingxiaotuan"
    monkeypatch.setenv("QXT_HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _isolated_environ(tmp_path, monkeypatch):
    """全局环境隔离: 让本机测试与 CI 行为一致。

    - QXT_HOME 默认指向临时目录: config.loader 的 load_dotenv 不再读取真实
      用户目录下的 ~/.qingxiaotuan/.env, 本机凭据不会泄漏进测试进程;
    - 清空所有 *API_KEY* 变量: 路由/供应商可用性只由各测试显式注入决定;
    - 清理可能泄漏的测试专用环境变量;
    - monkeypatch teardown 自动还原, 测试中途被灌进 os.environ 的变量一并回滚。
    需要特定变量的测试用 monkeypatch.setenv 自行覆盖即可。
    """
    monkeypatch.setenv("QXT_HOME", str(tmp_path / ".qingxiaotuan"))
    for key in [k for k in os.environ if "API_KEY" in k.upper()]:
        monkeypatch.delenv(key, raising=False)
    # Clean up test-specific env vars that might leak between tests
    for key in ("HOOK_AUDIT_LOG", "QXT_MODE", "QXT_WORKSPACE"):
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_i18n_language():
    """每个测试前把 i18n 语言重置回默认 zh-CN。

    解释: i18n 的 `_current` 是模块级全局。全量测试按字母序运行, 前面某个用例
    会调用 set_language('en')/ensure_language 而不再还原, 泄漏到后面依赖中文文案
    的用例 (如 /resume 的「已恢复会话」、banner「Welcome to 青小团 CLI!」)。
    隔离运行时这些用例各自通过, 全量却失败 —— 故需在此兜底复位, 保证确定性。
    """
    from qingxiaotuan.i18n import _current, DEFAULT_LANGUAGE
    if _current != DEFAULT_LANGUAGE:
        from qingxiaotuan.i18n import set_language
        set_language(DEFAULT_LANGUAGE)
    yield


# ---------------------------------------------------------------------------
# 共享: 本地 OpenAI 兼容 mock 端点 (供模型/CLI/worker 端到端测试复用)
# ---------------------------------------------------------------------------


class MockOpenAIServer:
    """极简 OpenAI 兼容端点: 按脚本逐次返回, 记录收到的请求。

    脚本元素是「assistant 消息规格」: {"content": ..., "tool_calls": [...], "finish_reason": ...}。
    服务端根据请求里的 stream 标志自动序列化为 JSON 或 SSE —— 与真实端点行为一致,
    避免「请求流式却收到 JSON」导致的解析空结果。
    """

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_cls())
        self.port = self._server.server_address[1]
        self.thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_cls(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静默
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                req = json.loads(body)
                with server._lock:
                    server.requests.append(req)
                stream = bool(req.get("stream"))
                with server._lock:
                    if not server.script:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(b'{"error":"script exhausted"}')
                        return
                    spec = server.script.pop(0)
                if stream:
                    self._send_sse(_spec_to_chunks(spec))
                else:
                    self._send_json(_spec_to_payload(spec))

            def _send_json(self, payload):
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_sse(self, chunks):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for chunk in chunks:
                    line = json.dumps(chunk, ensure_ascii=False)
                    self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        return Handler

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def mock_server():
    servers = []

    def factory(script):
        s = MockOpenAIServer(script).start()
        servers.append(s)
        return s

    yield factory
    for s in servers:
        s.stop()


def _chat_payload(content="", tool_calls=None, finish="stop", usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": 0,
        "model": "mock",
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5,
                           "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6},
    }


def _spec_to_payload(spec):
    """assistant 消息规格 → 非流式 JSON 响应。"""
    return _chat_payload(
        content=spec.get("content", ""),
        tool_calls=spec.get("tool_calls"),
        finish=spec.get("finish_reason", "stop"),
        usage=spec.get("usage"),
    )


def _spec_to_chunks(spec):
    """assistant 消息规格 → SSE chunk 序列 (工具调用分片累积, 内容逐字)。"""
    chunks = []
    tool_calls = spec.get("tool_calls") or []
    if tool_calls:
        for tc in tool_calls:
            fn = tc["function"]
            chunks.append({"id": "c1", "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": {"tool_calls": [
                    {"index": 0, "id": tc.get("id", "call_1"), "type": "function",
                     "function": {"name": fn["name"], "arguments": ""}}]},
                 "finish_reason": None}]})
            args = fn.get("arguments", "")
            # 把 arguments 切成两半, 验证流式累积
            mid = max(1, len(args) // 2)
            for piece in (args[:mid], args[mid:]):
                if not piece:
                    continue
                chunks.append({"id": "c1", "object": "chat.completion.chunk", "choices": [
                    {"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "function": {"arguments": piece}}]},
                     "finish_reason": None}]})
        chunks.append({"id": "c1", "object": "chat.completion.chunk", "choices": [
            {"index": 0, "delta": {}, "finish_reason": spec.get("finish_reason", "tool_calls")}]})
    else:
        content = spec.get("content", "")
        for piece in (content[:1], content[1:]):
            if not piece:
                continue
            chunks.append({"id": "c1", "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
        chunks.append({"id": "c1", "object": "chat.completion.chunk", "choices": [
            {"index": 0, "delta": {}, "finish_reason": spec.get("finish_reason", "stop")}]})
    return chunks


def _tool_call(name, arguments, cid="call_1"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}
