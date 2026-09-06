"""bench / config validate / session list 功能测试。"""

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.cli import commands
from qingxiaotuan.cli import cmd_setup, cmd_chat
from qingxiaotuan.config import Config


def _make_config(data: dict, key: str = None):
    config = Config.__new__(Config)
    config.data = data
    config.api_key = (lambda: key) if key is None else (lambda: key)
    return config


def test_validate_ok_config():
    config = _make_config({
        "model": {"provider": "deepseek", "model": "deepseek-chat",
                  "base_url": "https://api.deepseek.com",
                  "temperature": 0.7, "max_tokens": 8192,
                  "api_key_env": "DEEPSEEK_API_KEY"},
        "mode": {"default": "standard"},
        "agent": {"effort": "high", "max_iterations": 30},
        "context": {"budget_tokens": 60000},
    }, key="sk-test")
    issues = commands._validate_config(config)
    errs = [i for i in issues if i[0] == "err"]
    warns = [i for i in issues if i[0] == "warn"]
    assert not errs, errs
    assert not warns, warns


def test_validate_catches_bad_values():
    config = _make_config({
        "model": {"provider": "unknown-gw", "model": "", "base_url": "not-a-url",
                  "temperature": 9, "max_tokens": -1, "api_key_env": "NOPE"},
        "mode": {"default": "crazy"},
        "agent": {"effort": "insane", "max_iterations": 0},
        "context": {"budget_tokens": -5},
    })
    issues = commands._validate_config(config)
    errs = {i[1] for i in issues if i[0] == "err"}
    warns = {i[1] for i in issues if i[0] == "warn"}
    # 未知供应商但给了 base_url → 视为自定义网关 (warn); base_url 非法 → err
    assert "model.provider" in warns
    assert "model.model" in errs
    assert "model.base_url" in errs
    assert "model.temperature" in errs
    assert "model.max_tokens" in errs
    assert "API Key" in errs
    assert "mode.default" in errs
    assert "agent.effort" in errs
    assert "agent.max_iterations" in errs
    assert "context.budget_tokens" in errs


def test_validate_unknown_provider_with_url_is_warn():
    config = _make_config({
        "model": {"provider": "my-gateway", "model": "m",
                  "base_url": "https://gw.example.com/v1",
                  "temperature": 0.5, "max_tokens": 1000,
                  "api_key_env": "X"},
        "mode": {"default": "standard"},
        "agent": {"effort": "high", "max_iterations": 30},
        "context": {"budget_tokens": 60000},
    }, key="k")
    issues = commands._validate_config(config)
    levels = {i[1]: i[0] for i in issues}
    assert levels["model.provider"] == "warn"
    assert not [i for i in issues if i[0] == "err"]


def test_format_session_time():
    now = 1_000_000_000.0
    assert commands._format_session_time(now - 30, now) == "刚刚"
    assert commands._format_session_time(now - 300, now) == "5 分钟前"
    assert commands._format_session_time(now - 7200, now) == "2 小时前"
    assert commands._format_session_time(now - 3 * 86400, now) == "3 天前"
    assert commands._format_session_time(now - 30 * 86400, now).startswith("20")


def test_count_session_messages(tmp_path):
    f = tmp_path / "s.jsonl"
    lines = [
        {"type": "session.meta", "task": "t"},
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "yo"}},
        {"type": "tool_call", "name": "read_file"},
        {"type": "tool", "message": {"role": "tool", "content": "ok"}},
        {"type": "garbage", "message": {}},
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    assert commands._count_session_messages(f) == 3  # user + assistant + tool


def test_session_list_respects_qxt_home(tmp_path, monkeypatch, capsys):
    """qxt session list 应读取 QXT_HOME 下的会话目录, 而非硬编码 ~/.qingxiaotuan。"""
    home = tmp_path / "custom_home"
    monkeypatch.setenv("QXT_HOME", str(home))
    sess_dir = home / "sessions"
    sess_dir.mkdir(parents=True)
    sid = "20260826-test-session"
    (sess_dir / f"{sid}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    args = mock.Mock(session_cmd="list")
    assert commands.cmd_session(args) == 0
    out = capsys.readouterr().out
    assert sid in out


def test_bench_cache_graceful_without_key():
    """无 API Key 时应优雅提示并返回 1, 不抛异常。"""
    args = mock.Mock(bench_cmd="cache", rounds=2, task="t")
    with mock.patch.object(cmd_setup, "build_kernel") as bk:
        kernel = mock.Mock()
        config = mock.Mock()
        config.get = lambda k, d=None: {
            "model.provider": "deepseek", "model.model": "deepseek-chat"
        }.get(k, d)
        config.api_key = lambda: None
        kernel.require = lambda s: config
        bk.return_value = kernel
        assert commands.cmd_bench(args) == 1


def test_bench_cache_shows_per_round_deltas(tmp_path, monkeypatch, capsys):
    """逐轮明细应显示单轮增量而非累计值, 汇总用总累计。"""
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    args = mock.Mock(bench_cmd="cache", rounds=2, task="t")

    class FakeAgent:
        total_usage = {}

        def __init__(self):
            self._seq = [
                {"prompt_tokens": 100, "completion_tokens": 10,
                 "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100},
                {"prompt_tokens": 250, "completion_tokens": 20,
                 "prompt_cache_hit_tokens": 120, "prompt_cache_miss_tokens": 130},
            ]
            self.i = 0

        def run(self, prompt, stream=False):
            # 真实 Agent 的 total_usage 是跨轮累加的, 这里模拟同样行为
            for k, v in self._seq[self.i].items():
                self.total_usage[k] = self.total_usage.get(k, 0) + v
            self.i += 1

    with mock.patch.object(cmd_setup, "build_kernel") as bk, \
         mock.patch.object(cmd_setup, "create_agent", return_value=FakeAgent()):
        kernel = mock.Mock()
        config = mock.Mock()
        config.get = lambda k, d=None: {
            "model.provider": "deepseek", "model.model": "deepseek-chat"
        }.get(k, d)
        config.api_key = lambda: "sk-x"
        kernel.require = lambda s: config
        bk.return_value = kernel
        assert commands.cmd_bench(args) == 0
    out = capsys.readouterr().out
    # 第二轮增量 prompt = 250 (而非累计 350)
    assert "250" in out
    # 汇总总 prompt = 100+250 = 350
    assert "350" in out


def test_bench_unknown_subcommand_graceful():
    args = mock.Mock(bench_cmd="nope")
    assert commands.cmd_bench(args) == 0


# ------------------------------------------------------------------ model test

def test_model_test_without_key_returns_1():
    config = _make_config({"model": {"provider": "deepseek", "model": "deepseek-chat",
                                     "base_url": "https://api.deepseek.com"}})
    assert commands._model_test(config) == 1


def test_model_test_success():
    config = _make_config({"model": {"provider": "deepseek", "model": "deepseek-chat",
                                     "base_url": "https://api.deepseek.com"}}, key="sk-x")
    fake_resp = mock.Mock()
    fake_resp.content = "pong"
    fake_resp.usage = {"prompt_tokens": 5, "completion_tokens": 1}
    with mock.patch("qingxiaotuan.models.create_adapter") as ca:
        adapter = mock.Mock()
        adapter.chat.return_value = fake_resp
        ca.return_value = adapter
        assert commands._model_test(config) == 0
        adapter.chat.assert_called_once()


def test_model_test_connection_error():
    config = _make_config({"model": {"provider": "deepseek", "model": "deepseek-chat",
                                     "base_url": "https://api.deepseek.com"}}, key="sk-x")
    with mock.patch("qingxiaotuan.models.create_adapter") as ca:
        ca.side_effect = RuntimeError("boom")
        assert commands._model_test(config) == 1


# ------------------------------------------------------------------ setup 向导

def test_ask_choice_defaults():
    with mock.patch("builtins.input", side_effect=[""]):
        assert commands._ask_choice("p", ["a", "b"], "a") == "a"
    with mock.patch("builtins.input", side_effect=["b"]):
        assert commands._ask_choice("p", ["a", "b"], "a") == "b"
    with mock.patch("builtins.input", side_effect=["zzz"]):
        assert commands._ask_choice("p", ["a", "b"], "a") == "a"


def test_setup_blank_creates_skeleton(tmp_path, monkeypatch):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    config = Config()
    assert commands._setup_blank(config) == 0
    home = tmp_path / "home"
    for sub in ["memories", "skills", "sessions", "logs", "cron", "profiles", "history"]:
        assert (home / sub).is_dir()
    assert (home / ".env").exists()


def test_setup_full_writes_all_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    config = Config()
    written = {}

    def fake_set_user(self, dotted, value):
        written[dotted] = value
        keys = dotted.split(".")
        node = self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    inputs = ["1", "", "yolo", "low", "0.5", "4096"]
    with mock.patch.object(Config, "set_user", fake_set_user), \
         mock.patch("builtins.input", side_effect=inputs), \
         mock.patch.object(cmd_chat, "_pick_model_interactive", return_value=("deepseek", "deepseek-chat")):
        assert commands._setup_full(config) == 0
    assert written["model.provider"] == "deepseek"
    assert written["model.model"] == "deepseek-chat"
    assert written["model.temperature"] == 0.5
    assert written["model.max_tokens"] == 4096
    assert written["mode.default"] == "yolo"
    assert written["agent.effort"] == "low"


def test_setup_full_invalid_inputs_fall_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    config = Config()
    written = {}

    def fake_set_user(self, dotted, value):
        written[dotted] = value
        keys = dotted.split(".")
        node = self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    # 非法枚举/数值 → 回退默认 (mode=standard, effort=high, temp=0.7, mt=8192)
    inputs = ["1", "", "crazy", "insane", "abc", "abc"]
    with mock.patch.object(Config, "set_user", fake_set_user), \
         mock.patch("builtins.input", side_effect=inputs), \
         mock.patch.object(cmd_chat, "_pick_model_interactive", return_value=("deepseek", "deepseek-chat")):
        assert commands._setup_full(config) == 0
    assert written["mode.default"] == "standard"
    assert written["agent.effort"] == "high"
    assert written["model.temperature"] == 0.7
    assert written["model.max_tokens"] == 8192


# ------------------------------------------------------------------ session delete

def test_resolve_session_target(tmp_path):
    files = [tmp_path / "aaa.jsonl", tmp_path / "bbb.jsonl", tmp_path / "ccc.jsonl"]
    assert commands._resolve_session_target(files, "all") == files
    assert commands._resolve_session_target(files, "2") == [files[1]]
    assert commands._resolve_session_target(files, "9") == []
    assert commands._resolve_session_target(files, "bb") == [files[1]]
    assert commands._resolve_session_target(files, "zz") == []


def _make_sessions(tmp_path, names, mtimes):
    sess_dir = tmp_path / "home" / "sessions"
    sess_dir.mkdir(parents=True)
    for name, mt in zip(names, mtimes):
        f = sess_dir / f"{name}.jsonl"
        f.write_text("{}", encoding="utf-8")
        import os
        os.utime(f, (mt, mt))
    return sess_dir


def test_session_delete_by_number(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    # mtime 越大越新 → 排序: s2(3000), s1(2000), s0(1000)
    sess_dir = _make_sessions(tmp_path, ["s0", "s1", "s2"], [1000, 2000, 3000])
    args = mock.Mock(session_cmd="delete", target="2", yes=True)
    assert commands.cmd_session(args) == 0
    remaining = sorted(f.name for f in sess_dir.glob("*.jsonl"))
    assert remaining == ["s0.jsonl", "s2.jsonl"]
    out = capsys.readouterr().out
    assert "已删除 1 个会话" in out


def test_session_delete_all_with_confirm(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    sess_dir = _make_sessions(tmp_path, ["a", "b"], [1000, 2000])
    args = mock.Mock(session_cmd="delete", target="all", yes=False)
    with mock.patch("builtins.input", side_effect=["y"]):
        assert commands.cmd_session(args) == 0
    assert not list(sess_dir.glob("*.jsonl"))


def test_session_delete_cancel(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    sess_dir = _make_sessions(tmp_path, ["a"], [1000])
    args = mock.Mock(session_cmd="delete", target="all", yes=False)
    with mock.patch("builtins.input", side_effect=["n"]):
        assert commands.cmd_session(args) == 0
    assert (sess_dir / "a.jsonl").exists()
    assert "已取消" in capsys.readouterr().out


def test_session_delete_not_found(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    _make_sessions(tmp_path, ["a"], [1000])
    args = mock.Mock(session_cmd="delete", target="zzz", yes=True)
    assert commands.cmd_session(args) == 1


def test_session_delete_by_id_prefix(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    sess_dir = _make_sessions(tmp_path, ["20260826-abc", "20260826-def"], [1000, 2000])
    args = mock.Mock(session_cmd="delete", target="20260826-def", yes=True)
    assert commands.cmd_session(args) == 0
    assert not (sess_dir / "20260826-def.jsonl").exists()
    assert (sess_dir / "20260826-abc.jsonl").exists()


# ------------------------------------------------------------------ model test 覆盖参数

def test_model_test_override_params():
    """--provider/--model/--base-url 应临时覆盖而不写盘。"""
    config = _make_config({"model": {"provider": "deepseek", "model": "deepseek-chat",
                                     "base_url": "https://api.deepseek.com"}}, key="sk-x")
    args = mock.Mock(provider="my-gw", model="m1", base_url="https://gw.example.com/v1",
                     api_key_env=None)
    fake_resp = mock.Mock()
    fake_resp.content = "pong"
    fake_resp.usage = {"prompt_tokens": 5, "completion_tokens": 1}
    with mock.patch("qingxiaotuan.models.create_adapter") as ca:
        adapter = mock.Mock()
        adapter.chat.return_value = fake_resp
        ca.return_value = adapter
        assert commands._model_test(config, args) == 0
        view = ca.call_args[0][0]
        assert view.get("model.provider") == "my-gw"
        assert view.get("model.model") == "m1"
        assert view.get("model.base_url") == "https://gw.example.com/v1"
        # 未覆盖的键仍走基础配置
        assert view.get("model.temperature", 0.7) == 0.7


def test_model_test_override_key_env(monkeypatch):
    """--api-key-env 覆盖后应读取该环境变量。"""
    monkeypatch.setenv("MY_GW_KEY", "sk-gw")
    config = _make_config({"model": {"provider": "deepseek", "model": "deepseek-chat",
                                     "base_url": "https://api.deepseek.com"}})
    args = mock.Mock(provider="my-gw", model="m1", base_url="https://gw.example.com/v1",
                     api_key_env="MY_GW_KEY")
    fake_resp = mock.Mock()
    fake_resp.content = "pong"
    fake_resp.usage = {}
    with mock.patch("qingxiaotuan.models.create_adapter") as ca:
        adapter = mock.Mock()
        adapter.chat.return_value = fake_resp
        ca.return_value = adapter
        assert commands._model_test(config, args) == 0


# ------------------------------------------------------------------ bench latency

def test_bench_latency_graceful_without_key():
    """无 API Key 时应优雅提示并返回 1。"""
    args = mock.Mock(bench_cmd="latency", rounds=2, task="t")
    with mock.patch.object(cmd_setup, "build_kernel") as bk:
        kernel = mock.Mock()
        config = mock.Mock()
        config.get = lambda k, d=None: {
            "model.provider": "deepseek", "model.model": "deepseek-chat"
        }.get(k, d)
        config.api_key = lambda: None
        kernel.require = lambda s: config
        bk.return_value = kernel
        assert commands.cmd_bench(args) == 1


def test_bench_latency_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    args = mock.Mock(bench_cmd="latency", rounds=2, task="t")
    with mock.patch.object(cmd_setup, "build_kernel") as bk, \
         mock.patch("qingxiaotuan.models.create_adapter") as ca:
        kernel = mock.Mock()
        config = mock.Mock()
        config.get = lambda k, d=None: {
            "model.provider": "deepseek", "model.model": "deepseek-chat"
        }.get(k, d)
        config.api_key = lambda: "sk-x"
        kernel.require = lambda s: config
        bk.return_value = kernel
        adapter = mock.Mock()
        resp = mock.Mock()
        resp.usage = {"completion_tokens": 10}
        adapter.chat.return_value = resp
        ca.return_value = adapter
        assert commands.cmd_bench(args) == 0
    out = capsys.readouterr().out
    assert "平均延迟" in out
    assert "吞吐" in out
    assert "总输出 token" in out


# ------------------------------------------------------------------ doctor

def test_doctor_reports_config_errors(tmp_path, monkeypatch, capsys):
    """doctor 复用 _validate_config, 有 err 返回 1。"""
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    args = mock.Mock(workspace=str(tmp_path))
    with mock.patch.object(cmd_chat, "build_kernel") as bk, \
         mock.patch.object(cmd_chat, "_validate_config", return_value=[
             ("ok", "model.provider", "deepseek"),
             ("err", "model.model", "未设置模型名"),
         ]), \
         mock.patch("qingxiaotuan.ext.registry.engine_healthcheck",
                    return_value={"diff": {"ok": True}}), \
         mock.patch("httpx.Client") as hc, \
         mock.patch.object(cmd_chat.subprocess, "run") as sp:
        kernel = mock.Mock()
        config = mock.Mock()
        config.get = lambda k, d=None: None
        config.api_key = lambda: None
        kernel.require = lambda s: config
        bk.return_value = kernel
        fake_client = mock.MagicMock()
        fake_client.__enter__.return_value = fake_client
        hc.return_value = fake_client
        sp.return_value = mock.Mock(returncode=0, stdout="")
        assert commands.cmd_doctor(args) == 1
    out = capsys.readouterr().out
    assert "✗" in out
    assert "发现 1 个错误" in out


def test_doctor_all_ok(tmp_path, monkeypatch, capsys):
    """全部正常时返回 0 并显示「全部正常」。"""
    monkeypatch.setenv("QXT_HOME", str(tmp_path / "home"))
    args = mock.Mock(workspace=str(tmp_path))
    with mock.patch.object(cmd_chat, "build_kernel") as bk, \
         mock.patch.object(cmd_chat, "_validate_config", return_value=[
             ("ok", "model.provider", "deepseek"),
             ("ok", "model.model", "deepseek-chat"),
         ]), \
         mock.patch("qingxiaotuan.ext.registry.engine_healthcheck",
                    return_value={"diff": {"ok": True}}), \
         mock.patch("httpx.Client") as hc, \
         mock.patch("qingxiaotuan.models.create_adapter") as ca, \
         mock.patch("urllib.request.urlopen") as urlopen, \
         mock.patch.object(cmd_chat.subprocess, "run") as sp:
        kernel = mock.Mock()
        config = mock.Mock()
        config.get = lambda k, d=None: {
            "model.base_url": "https://api.deepseek.com"
        }.get(k, d)
        config.api_key = lambda: "sk-x"
        kernel.require = lambda s: config
        bk.return_value = kernel
        fake_client = mock.MagicMock()
        fake_client.__enter__.return_value = fake_client
        hc.return_value = fake_client
        urlopen.return_value.__enter__.return_value = mock.Mock()
        adapter = mock.Mock()
        adapter.chat.return_value = mock.Mock()
        ca.return_value = adapter
        sp.return_value = mock.Mock(returncode=0, stdout="")
        assert commands.cmd_doctor(args) == 0
    out = capsys.readouterr().out
    assert "全部正常" in out
