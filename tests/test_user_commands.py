"""自定义斜杠命令 (<QXT_HOME>/commands 与 <workspace>/.qxt/commands) 测试。

验证:
- 两级目录扫描 + 项目级覆盖同名用户级;
- frontmatter 解析 (description / argument-hint / name 回落文件名);
- 模板展开: $ARGUMENTS、$1..$9、@file 注入 (含截断)、!`cmd` 预执行;
- install_user_commands 包装 _handle_slash: 命中自定义命令跑一轮对话
  (EchoModel 回声), 未命中回落原链 (/help 仍工作), 重复安装幂等;
- 补全列表与 /help 追加。
"""

import json

from qingxiaotuan.app import build_kernel
from qingxiaotuan.cli import user_commands as uc_mod
from qingxiaotuan.cli import commands as cmds_mod
from qingxiaotuan.core.agent import Agent
from qingxiaotuan.models.base import ModelAdapter, ModelResponse


class EchoModel(ModelAdapter):
    """单轮: 把最后一条 user 消息回声作为答案。"""
    name = "echo"

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        last = next((m["content"] for m in reversed(messages)
                     if m.get("role") == "user" and m.get("content")), "")
        return ModelResponse(content=f"回声: {last[:200]}")


def _write_cmd(home, name: str, text: str) -> None:
    d = home / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


DEPLOY = (
    "---\n"
    "name: deploy\n"
    "description: 部署到指定环境\n"
    "argument-hint: <env>\n"
    "---\n\n"
    "请把应用部署到 $1 环境, 备注: $ARGUMENTS。当前分支: !`echo main-branch`"
)


# ------------------------------------------------------------------ 加载与解析

def test_load_two_levels_and_override(qxt_home, tmp_path):
    _write_cmd(qxt_home, "deploy.md", DEPLOY)
    ws = tmp_path / "ws"
    proj = ws / ".qxt" / "commands"
    proj.mkdir(parents=True)
    (proj / "deploy.md").write_text(
        "---\ndescription: 项目级覆盖\n---\n项目版: $ARGUMENTS", encoding="utf-8")
    table = uc_mod.load_user_commands("no-such-config", str(ws))
    assert "deploy" in table
    assert table["deploy"].description == "项目级覆盖"      # 就近优先


def test_parse_name_fallback_and_body(qxt_home):
    _write_cmd(qxt_home, "helper.md", "你就是个帮手, 目标: $ARGUMENTS")
    table = uc_mod.load_user_commands(None, "")
    uc = table["helper"]
    assert uc.name == "helper" and uc.argument_hint == ""
    assert "$ARGUMENTS" in uc.body


def test_expand_arguments_and_shell(tmp_path, qxt_home):
    uc = uc_mod.parse_command.__wrapped__ if False else None  # noqa: F841 (占位防误删注释)
    _write_cmd(qxt_home, "deploy.md", DEPLOY)
    cmd = uc_mod.load_user_commands(None, "")["deploy"]
    out = uc_mod.expand_command(cmd, "prod 快一点", workspace=str(tmp_path))
    assert "部署到 prod 环境" in out                 # $1
    assert "备注: prod 快一点" in out                # $ARGUMENTS
    assert "main-branch" in out                      # !`cmd` stdout 替换
    assert "!`" not in out and "$1" not in out


def test_expand_file_inject_and_truncation(tmp_path, qxt_home):
    note = tmp_path / "notes.md"
    note.write_text("A" * 60_000, encoding="utf-8")
    _write_cmd(qxt_home, "review.md",
               "审查 @notes.md 并总结, 参数: $1")
    cmd = uc_mod.load_user_commands(None, str(tmp_path))["review"]
    out = uc_mod.expand_command(cmd, "严格", workspace=str(tmp_path))
    assert '<file path="notes.md">' in out
    assert "截断" in out                              # 超限截断保护
    # 不存在的文件原样保留
    _write_cmd(qxt_home, "ghost.md", "看看 @no_such_file.md")
    out2 = uc_mod.expand_command(
        uc_mod.load_user_commands(None, "")["ghost"], "", workspace=str(tmp_path))
    assert "@no_such_file.md" in out2


# ------------------------------------------------------------------ 安装与分发

def _make_agent(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", EchoModel(), owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda _p: True)
    return kernel, config, agent


def test_install_dispatches_custom_command(tmp_path, qxt_home, monkeypatch):
    _write_cmd(qxt_home, "deploy.md", "请部署 $1, 完毕。")
    kernel, config, agent = _make_agent(tmp_path, qxt_home)
    n = uc_mod.install_user_commands(agent, config, str(tmp_path))
    assert n >= 1 and "/deploy" in cmds_mod._HELP

    captured: list = []
    monkeypatch.setattr(cmds_mod, "_run_turn",
                        lambda ag, text, cfg: captured.append(text))
    handled = cmds_mod._handle_slash("/deploy 生产", agent, config, str(tmp_path))
    assert handled is True
    assert len(captured) == 1 and "部署 生产" in captured[0]


def test_unmatched_falls_through(tmp_path, qxt_home, monkeypatch):
    kernel, config, agent = _make_agent(tmp_path, qxt_home)
    uc_mod.install_user_commands(agent, config, str(tmp_path))
    # 未命中的命令回落原处理链: /help 正常返回且不触发回合
    captured: list = []
    monkeypatch.setattr(cmds_mod, "_run_turn",
                        lambda ag, text, cfg: captured.append(text))
    assert cmds_mod._handle_slash("/help", agent, config, str(tmp_path)) is True
    assert captured == []


def test_double_install_is_idempotent(tmp_path, qxt_home, monkeypatch):
    kernel, config, agent = _make_agent(tmp_path, qxt_home)
    uc_mod.install_user_commands(agent, config, str(tmp_path))
    first = cmds_mod._handle_slash
    uc_mod.install_user_commands(agent, config, str(tmp_path))
    assert cmds_mod._handle_slash is first             # 不叠加包装
    # 新增文件后重装 → 表格刷新生效
    _write_cmd(qxt_home, "extra.md", "额外命令")
    uc_mod.install_user_commands(agent, config, str(tmp_path))
    captured: list = []
    monkeypatch.setattr(cmds_mod, "_run_turn",
                        lambda ag, text, cfg: captured.append(text))
    assert cmds_mod._handle_slash("/extra", agent, config, str(tmp_path)) is True
