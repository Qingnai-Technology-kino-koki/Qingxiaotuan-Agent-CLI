"""多 Agent 角色配置 (model.planner / model.worker) 的存储往返测试。"""

from __future__ import annotations

import pytest

from qingxiaotuan.config.loader import Config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    home = tmp_path / "qxt"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QXT_HOME", str(home))
    return Config()


def test_set_model_planner_role(isolated_config: Config) -> None:
    """/model planner 写入的 model.planner 持久化且可回读。"""
    isolated_config.set_user("model.planner", {"provider": "claude-gw", "model": "claude-3-5"})
    assert isolated_config.get("model.planner.provider") == "claude-gw"
    assert isolated_config.get("model.planner.model") == "claude-3-5"
    # 不影响主模型
    assert isolated_config.get("model.provider") == "deepseek"


def test_set_model_worker_role(isolated_config: Config) -> None:
    """/model worker 写入的 model.worker 持久化且可回读。"""
    isolated_config.set_user("model.worker", {
        "provider": "opencode-zen", "model": "deepseek-v4-flash-free",
        "base_url": "https://opencode.ai/zen/v1", "api_key_env": "OPENCODE_ZEN_API_KEY",
    })
    w = isolated_config.get("model.worker", {})
    assert w["provider"] == "opencode-zen"
    assert w["model"] == "deepseek-v4-flash-free"
    assert w["base_url"] == "https://opencode.ai/zen/v1"


def test_role_overwrite_merges(isolated_config: Config) -> None:
    """两次配置同一角色, 后者覆盖而非深合并残留旧字段。"""
    isolated_config.set_user("model.planner", {"provider": "a", "model": "m1"})
    isolated_config.set_user("model.planner", {"provider": "b"})
    assert isolated_config.get("model.planner.provider") == "b"
    # 旧 model 字段应被整值替换掉 (patch 语义)
    assert isolated_config.get("model.planner.model") is None
