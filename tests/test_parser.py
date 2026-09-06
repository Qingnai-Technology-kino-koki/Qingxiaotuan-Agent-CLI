"""qxt CLI 参数解析与命令注册测试 (cli/parser.py)。

验证:
- 每个顶层子命令都注册了 func 默认值 (main() 依赖它做分发);
- 子命令 -> func 的映射正确;
- 带嵌套子命令的父命令在缺省子命令时报错 (required=True);
- 各子命令的必需参数 / 可选 flag 解析正确;
- _resolve_func 把函数名映射到正确的模块 (ext/improve 走独立模块);
- main() 的分发逻辑 (无子命令 -> 交互对话(chat), --yolo -> mode=yolo, 非 TTY 拦截)。
"""

import argparse
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from qingxiaotuan.cli import parser
from qingxiaotuan.cli.parser import build_parser


def _parse(*argv):
    return build_parser().parse_args(list(argv))


# ---------------------------------------------------------------- 子命令注册

def test_all_subcommands_have_func_default():
    """每个顶层子命令都必须注册 func, 否则 main() 无法分发。

    ext/improve 例外: 它们是"必需子命令"父命令, func 由叶子子命令设置。
    """
    parser_obj = build_parser()
    sub_actions = [a for a in parser_obj._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(sub_actions) == 1
    for name, sub in sub_actions[0].choices.items():
        if name in ("ext", "improve"):
            continue
        assert "func" in sub._defaults, f"子命令 {name} 缺少 func 默认值"


def test_subcommand_func_values():
    cases = {
        "dev": ("cmd_dev", ["task"]),
        "run": ("cmd_run", ["task"]),
        "agent": ("cmd_agent", ["task"]),
        "bg": ("cmd_bg", ["list"]),
        "bench": ("cmd_bench", ["cache"]),
        "setup": ("cmd_setup", []),
        "doctor": ("cmd_doctor", []),
        "mode": ("cmd_mode", []),
        "models": ("cmd_model", []),
        "config": ("cmd_config", ["dump"]),
        "plugin": ("cmd_plugin", ["list"]),
        "skill": ("cmd_skill", ["list"]),
        "memory": ("cmd_memory", ["list"]),
        "cron": ("cmd_cron", ["list"]),
        "open": ("cmd_open", ["file.py"]),
        "mcp": ("cmd_mcp", ["list"]),
        "hooks": ("cmd_hooks", ["list"]),
        "undo": ("cmd_undo", []),
        "impact": ("cmd_impact", []),
        "session": ("cmd_session", ["list"]),
    }
    for name, (func, extra) in cases.items():
        assert _parse(name, *extra).func == func, f"{name} -> {func}"


def test_ext_and_improve_leaves_set_func():
    """ext/improve 父命令无 func, 叶子子命令负责设置。"""
    assert _parse("ext", "engines").func == "cmd_ext"
    assert _parse("ext", "call", "crypto", "load").func == "cmd_ext"
    assert _parse("improve", "summarize").func == "cmd_improve"
    assert _parse("improve", "apply").func == "cmd_improve"


def test_nested_subcommands_required():
    """带嵌套子命令的父命令必须提供子命令 (models 例外: 无子命令进交互选择)。"""
    for parent in ["bg", "bench", "config", "plugin", "skill", "memory",
                   "cron", "mcp", "hooks", "ext", "improve", "session"]:
        with pytest.raises(SystemExit):
            _parse(parent)


def test_model_without_subcommand_is_valid():
    args = _parse("models")
    assert args.func == "cmd_model"
    assert getattr(args, "model_cmd", None) is None


# ---------------------------------------------------------------- 参数解析

def test_run_parses_task_and_flags():
    args = _parse("run", "do the thing", "--yes", "--no-stream", "--model", "m")
    assert args.task == "do the thing"
    assert args.yes is True
    assert args.no_stream is True
    assert args.model == "m"


def test_dev_requires_task():
    with pytest.raises(SystemExit):
        _parse("dev")


def test_cron_add_parses_interval_and_output():
    args = _parse("cron", "add", "nightly", "run report", "--interval", "120", "--output", "r.txt")
    assert args.name == "nightly"
    assert args.prompt == "run report"
    assert args.interval == 120
    assert args.output == "r.txt"


def test_open_parses_target():
    args = _parse("open", "src/main.py:42")
    assert args.target == "src/main.py:42"


def test_model_set_parses_positional():
    args = _parse("models", "set", "deepseek", "deepseek-chat")
    assert args.provider == "deepseek"
    assert args.model == "deepseek-chat"
    assert args.base_url is None
    assert args.api_key_env is None


def test_mcp_call_parses_json():
    args = _parse("mcp", "call", "srv", "tool", "--json", '{"text":"hi"}')
    assert args.server == "srv"
    assert args.tool == "tool"
    assert args.json == '{"text":"hi"}'


def test_session_resume_optional_target():
    assert _parse("session", "resume").target is None
    assert _parse("session", "resume", "3").target == "3"


def test_undo_default_target():
    assert _parse("undo").target == ""


def test_version_flag_exits_zero():
    with pytest.raises(SystemExit) as e:
        _parse("--version")
    assert e.value.code == 0


# ---------------------------------------------------------------- 函数解析与分发

def test_resolve_func_routes_to_correct_module():
    with mock.patch("qingxiaotuan.cli.parser.importlib.import_module") as imp:
        parser._resolve_func("cmd_ext")
        assert imp.call_args[0][0] == ".ext_cli"
        parser._resolve_func("cmd_improve")
        assert imp.call_args[0][0] == ".improve_cli"
        parser._resolve_func("cmd_chat")
        assert imp.call_args[0][0] == ".commands"


def test_main_no_args_routes_to_interactive():
    """`qxt` (无子命令) 应进入交互启动 (fast_chat -> cmd_chat)。"""
    from qingxiaotuan.cli import fast_start
    captured = {}
    def spy(args):
        captured["called"] = True
        return 0
    with mock.patch.object(fast_start, "fast_chat", spy), \
         mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch.object(sys, "argv", ["qxt"]):
        assert parser.main() == 0
    assert captured["called"] is True


def test_main_yolo_sets_mode():
    from qingxiaotuan.cli import fast_start
    captured = {}
    def spy(args):
        captured["mode"] = getattr(args, "mode", None)
        return 0
    with mock.patch.object(fast_start, "fast_chat", spy), \
         mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch.object(sys, "argv", ["qxt", "--yolo"]):
        parser.main()
    assert captured["mode"] == "yolo"


def test_main_routes_run_to_cmd_run():
    from qingxiaotuan.cli import commands
    captured = {}
    def spy(args):
        captured["task"] = args.task
        return 0
    with mock.patch.object(commands, "cmd_run", spy), \
         mock.patch("sys.stdin.isatty", return_value=False), \
         mock.patch.object(sys, "argv", ["qxt", "run", "do it"]):
        assert parser.main() == 0
    assert captured["task"] == "do it"


def test_main_returns_2_when_not_tty_and_interactive():
    with mock.patch("sys.stdin.isatty", return_value=False), \
         mock.patch.object(sys, "argv", ["qxt"]):
        assert parser.main() == 2
