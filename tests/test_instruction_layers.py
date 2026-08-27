"""分层项目级指令发现 (对标 Claude Code 的 CLAUDE.md 分层记忆) 测试。

覆盖三层:
- 用户全局层: <qxt_home>/AGENTS.md 与 ~/.agents/AGENTS.md (monkeypatch 隔离);
- 项目祖先层: 工作区沿父目录向上到最近 git 仓库根, 逐级收录;
- 工作区根: 候选名优先级 (QXT.md > AGENTS.md > CLAUDE.md > .qxt.md)。

以及两条硬约束: 绝不飘出 git 仓库边界; 同环境多次构建逐字节一致 (缓存友好)。
"""

from pathlib import Path

import qingxiaotuan.core.prompts as prompts
from qingxiaotuan.core.prompts import (
    _discover_project_instructions,
    build_system_prompt,
)


def test_global_layer_from_qxt_home(qxt_home, tmp_path):
    """<qxt_home>/AGENTS.md 应作为用户全局层注入。"""
    qxt_home.mkdir(parents=True, exist_ok=True)
    (qxt_home / "AGENTS.md").write_text("全局约定: 中文注释", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    out = _discover_project_instructions(str(ws), home=qxt_home)
    assert "# 全局指令" in out
    assert "全局约定: 中文注释" in out


def test_global_layer_user_agents_fallback(qxt_home, tmp_path, monkeypatch):
    """~/.agents/AGENTS.md (社区标准位置) 也应被收录 —— monkeypatch 隔离真实用户目录。"""
    fake = tmp_path / "userhome" / ".agents" / "AGENTS.md"
    fake.parent.mkdir(parents=True)
    fake.write_text("fallback 层内容", encoding="utf-8")
    monkeypatch.setattr(prompts, "_USER_GLOBAL_AGENTS", fake)
    ws = tmp_path / "ws"
    ws.mkdir()
    out = _discover_project_instructions(str(ws), home=tmp_path / "empty_home")
    assert "fallback 层内容" in out


def test_chain_within_git_repo(tmp_path):
    """仓库内祖先链: 根与子目录各有 AGENTS.md 时都收录, 由远及近排列。"""
    repo = tmp_path / "repo"
    sub = repo / "sub"
    sub.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "AGENTS.md").write_text("仓库根规则", encoding="utf-8")
    (sub / "AGENTS.md").write_text("子目录细则", encoding="utf-8")
    out = _discover_project_instructions(str(sub))
    # 两层都在
    assert "仓库根规则" in out
    assert "子目录细则" in out
    # 由远及近: 仓库根段在前, 工作区段在后; 祖先级带 ../ 前缀标注
    assert out.index("仓库根规则") < out.index("子目录细则")
    assert "# 项目指令 (../AGENTS.md)" in out
    assert "# 项目指令 (AGENTS.md)" in out


def test_no_escape_outside_repo(tmp_path, monkeypatch):
    """工作区不在 git 仓库内时绝不向上外溢读取祖先指令。

    注: pytest 的 tmp_path 通常落在本项目仓库内, 真实文件系统无法构造
    "仓库外"场景, 故用 monkeypatch 让所有目录都判为非仓库根。
    """
    plain = tmp_path / "plain"
    ws = plain / "ws"
    ws.mkdir(parents=True)
    (plain / "AGENTS.md").write_text("仓库外的规则", encoding="utf-8")
    monkeypatch.setattr(prompts, "_is_repo_root", lambda _p: False)
    out = _discover_project_instructions(str(ws))
    assert "仓库外的规则" not in out


def test_root_candidate_priority(tmp_path):
    """工作区根同时有 QXT.md 与 AGENTS.md 时都收录, 且 QXT.md 段在前。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "QXT.md").write_text("自家格式优先", encoding="utf-8")
    (ws / "AGENTS.md").write_text("社区标准其次", encoding="utf-8")
    out = _discover_project_instructions(str(ws))
    assert "自家格式优先" in out and "社区标准其次" in out
    assert out.index("自家格式优先") < out.index("社区标准其次")


def test_byte_stability_for_cache(qxt_home, tmp_path):
    """同环境多次构建 system prompt 必须逐字节一致 (prompt cache 硬要求)。"""
    qxt_home.mkdir(parents=True, exist_ok=True)
    (qxt_home / "AGENTS.md").write_text("稳定层", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("项目层", encoding="utf-8")
    p1 = build_system_prompt(home=qxt_home, workspace=str(ws))
    p2 = build_system_prompt(home=qxt_home, workspace=str(ws))
    assert p1 == p2
    assert "稳定层" in p1 and "项目层" in p1
