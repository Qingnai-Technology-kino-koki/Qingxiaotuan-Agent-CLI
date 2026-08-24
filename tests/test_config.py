"""组合式配置测试: 深合并、profile 叠加、patch 整值替换。"""

import yaml

from qingxiaotuan.config import Config, DEFAULT_CONFIG, deep_merge, patch_replace


def test_default_config_available(qxt_home):
    cfg = Config()
    assert cfg.get("model.provider") == "deepseek"
    assert cfg.get("agent.max_iterations") == 30


def test_deep_merge():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    out = deep_merge(base, {"a": {"y": 20}})
    assert out == {"a": {"x": 1, "y": 20}, "b": 3}
    assert base["a"]["y"] == 2  # 不修改原对象


def test_user_layer_overrides_default(qxt_home):
    home = qxt_home
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(yaml.safe_dump({"model": {"model": "deepseek-reasoner"}}))
    cfg = Config()
    assert cfg.get("model.model") == "deepseek-reasoner"
    assert cfg.get("model.base_url") == DEFAULT_CONFIG["model"]["base_url"]  # 未覆盖的保留


def test_profile_layer(qxt_home):
    pdir = qxt_home / "profiles" / "work"
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text(yaml.safe_dump({"agent": {"max_iterations": 99}}))
    assert Config(profile="work").get("agent.max_iterations") == 99
    assert Config(profile="default").get("agent.max_iterations") == 30


def test_patch_replaces_whole_value(qxt_home, tmp_path):
    patch_file = tmp_path / "p.yaml"
    patch_file.write_text(yaml.safe_dump({"model.model": "patched-model", "agent.max_iterations": 7}))
    cfg = Config(patch_file=str(patch_file))
    assert cfg.get("model.model") == "patched-model"
    assert cfg.get("agent.max_iterations") == 7


def test_set_user_persists(qxt_home):
    cfg = Config()
    cfg.set_user("model.temperature", 0.1)
    assert Config().get("model.temperature") == 0.1  # 重新加载仍在


def test_set_user_json_value_becomes_nested(qxt_home):
    """qxt config set model.worker '{"provider":"opencode-zen"}' 应存为嵌套对象,
    且未覆盖的字段从默认继承 (base_url/api_key_env)。"""
    cfg = Config()
    cfg.set_user("model.worker", '{"provider": "opencode-zen", "model": "deepseek-v4-flash-free"}')
    reloaded = Config()
    worker = reloaded.get("model.worker")
    assert worker["provider"] == "opencode-zen"
    assert worker["model"] == "deepseek-v4-flash-free"
    # 未显式提供的字段从 DEFAULT_CONFIG 继承
    assert worker["base_url"] == "https://opencode.ai/zen/v1"
    assert worker["api_key_env"] == "OPENCODE_ZEN_API_KEY"


def test_set_user_scalar_still_coerced(qxt_home):
    """标量字符串仍按原语义转换 (int/bool), 不受 JSON 分支影响。"""
    cfg = Config()
    cfg.set_user("agent.max_iterations", "50")
    assert Config().get("agent.max_iterations") == 50
    cfg.set_user("mode.default", "yolo")
    assert Config().get("mode.default") == "yolo"


def test_ensure_home_creates_hermes_layout(qxt_home):
    cfg = Config()
    cfg.ensure_home()
    assert (qxt_home / "SOUL.md").exists()
    assert (qxt_home / "memories" / "MEMORY.md").exists()
    assert (qxt_home / "memories" / "USER.md").exists()
    for sub in ["skills", "sessions", "logs", "cron", "profiles"]:
        assert (qxt_home / sub).is_dir()
