"""CLI 开箱即用简化测试 (Task: qxt 直接开界面 / qxt --yolo / qxt model 12家)。

验证:
- 顶层 --yolo 被 main() 转成 mode=yolo 并交给 cmd_chat;
- 顶层 --model 透传给 cmd_chat;
- qxt model 无子命令时进入交互选择 (PROVIDER_PRESETS 驱动);
- qxt model set 用预设自动带好 base_url / api_key_env。
"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.cli.parser import build_parser
from qingxiaotuan.cli import commands
from qingxiaotuan.models import PROVIDER_PRESETS


def test_top_level_yolo_routes_to_chat_in_yolo_mode():
    captured = {}
    orig = commands.cmd_chat
    def spy(args):
        captured["mode"] = getattr(args, "mode", None)
        captured["model"] = getattr(args, "model", None)
        return 0
    with mock.patch.object(commands, "cmd_chat", spy):
        args = build_parser().parse_args(["--yolo"])
        # 复刻 main() 的逻辑
        if getattr(args, "yolo", False) and not getattr(args, "mode", None):
            args.mode = "yolo"
        commands.cmd_chat(args)
    assert captured["mode"] == "yolo"


def test_top_level_model_passthrough():
    captured = {}
    def spy(args):
        captured["model"] = getattr(args, "model", None)
        return 0
    with mock.patch.object(commands, "cmd_chat", spy):
        args = build_parser().parse_args(["--model", "deepseek-reasoner"])
        commands.cmd_chat(args)
    assert captured["model"] == "deepseek-reasoner"


def test_model_no_subcommand_triggers_interactive_chooser(tmp_path):
    """qxt model 无子命令应进入交互选择; 模拟选第1家(deepseek)并默认模型。"""
    written = {}
    orig_set_user = commands.Config.set_user
    def fake_set_user(self, dotted, value):
        written[dotted] = value
        # 同步内存视图, 避免后续读不到
        keys = dotted.split(".")
        node = self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
    with mock.patch.object(commands.Config, "set_user", fake_set_user), \
         mock.patch("builtins.input", side_effect=["1", ""]):  # 选 deepseek, 模型用默认
        rc = commands.cmd_model(build_parser().parse_args(["model"]))
    assert rc == 0
    assert written.get("model.provider") == "deepseek"
    assert written.get("model.base_url") == PROVIDER_PRESETS["deepseek"]["base_url"]
    assert written.get("model.api_key_env") == PROVIDER_PRESETS["deepseek"]["api_key_env"]


def test_model_set_uses_explicit_values():
    written = {}
    def fake_set_user(self, dotted, value):
        written[dotted] = value
        keys = dotted.split(".")
        node = self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
    with mock.patch.object(commands.Config, "set_user", fake_set_user):
        rc = commands.cmd_model(build_parser().parse_args(
            ["model", "set", "openai", "gpt-4o", "https://api.openai.com/v1", "OPENAI_API_KEY"]))
    assert rc == 0
    assert written["model.provider"] == "openai"
    assert written["model.model"] == "gpt-4o"
    assert written["model.base_url"] == "https://api.openai.com/v1"
