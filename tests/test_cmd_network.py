"""测试: 联网/搜索配置命令 (cmd_network) + 别名解析。

覆盖:
- `qxt network configuration` / `qxt net con` 等别名均解析到 cmd_network;
- action 简写 (裸 key 视为 get);
- 不接网络, 只验证参数路由与 Command 对象行为。
"""

from __future__ import annotations

from qingxiaotuan.cli.cmd_network import (
    _KNOWN_ACTIONS,
    _NET_META,
    _ORDER,
    _coerce,
    cmd_network,
)
from qingxiaotuan.cli.parser import build_parser


class _Args:
    def __init__(self, action=None, key=None, value=None, profile="default", patch=None):
        self.action = action
        self.key = key
        self.value = value
        self.profile = profile
        self.patch = patch


def test_network_alias_forms_resolve_to_network_command():
    p = build_parser()
    for words in (["network", "configuration"], ["net", "con"],
                  ["network", "con"], ["net", "config"],
                  ["net", "con", "set", "search_top_k", "3"]):
        args = p.parse_args(words)
        assert args.func == "cmd_network"


def test_network_alias_actions_map_correctly():
    p = build_parser()
    assert p.parse_args(["net", "con"]).action == "list"
    assert p.parse_args(["net", "con", "dump"]).action == "dump"
    g = p.parse_args(["net", "con", "search_top_k"])   # 裸 key 简写
    assert g.action == "search_top_k" and g.key is None
    s = p.parse_args(["network", "con", "set", "search_max_results", "500"])
    assert s.action == "set" and s.key == "search_max_results" and s.value == "500"


def test_cmd_network_get_shortcut(monkeypatch):
    seen = {}

    class _Cfg:
        def get(self, dotted, default=None):
            seen["dotted"] = dotted
            return {"search_top_k": 7}.get(dotted, default)

        def set_user(self, key, value):  # pragma: no cover
            pass

    monkeypatch.setattr("qingxiaotuan.cli.cmd_network.Config",
                        lambda profile, patch_file=None: _Cfg())
    rc = cmd_network(_Args(action="search_top_k"))
    assert rc == 0  # 裸 key 走 get, 不报错


def test_cmd_network_list_and_known_actions():
    assert "search_top_k" in _ORDER
    assert {"list", "dump", "get", "set", "describe", "reset"} <= _KNOWN_ACTIONS


def test_coerce_validates_types_and_ranges():
    v, err = _coerce("search_max_results", "int", 1, 500, 501)
    assert v is None and "不能大于" in err
    v, err = _coerce("search_max_results", "int", 1, 500, 0)
    assert v is None and "不能小于" in err
    v, err = _coerce("search_top_k", "int", 0, None, "abc")
    assert v is None and "整数" in err
    assert _coerce("search_top_k", "int", 0, None, "3") == (3, "")
    assert _coerce("search_cache", "bool", None, None, "off") == (False, "")
    ok, err = _coerce("search_engines", "list", None, None, "duckduckgo bing")
    assert ok == ["duckduckgo", "bing"] and err == ""
    ok, err = _coerce("search_engines", "list", None, None, "google")
    assert ok is None and "未知引擎" in err


def test_cmd_network_set_rejects_invalid_value(monkeypatch):
    class _Cfg:
        def get(self, dotted, default=None):
            return default

        def set_user(self, key, value):
            raise AssertionError("非法值不应写入")

    monkeypatch.setattr("qingxiaotuan.cli.cmd_network.Config",
                        lambda profile, patch_file=None: _Cfg())
    assert cmd_network(_Args(action="set", key="search_max_results", value="9999")) == 1
    assert cmd_network(_Args(action="set", key="search_top_k", value="-1")) == 1


def test_cmd_network_reset_clears_user_overrides(monkeypatch):
    deleted: list[str] = []

    class _Cfg:
        def get(self, dotted, default=None):
            return {"search_top_k": 3}.get(dotted, default)

        def delete_user(self, dotted):
            deleted.append(dotted)
            return True

    monkeypatch.setattr("qingxiaotuan.cli.cmd_network.Config",
                        lambda profile, patch_file=None: _Cfg())
    # reset 指定 key
    cmd_network(_Args(action="reset", key="search_top_k"))
    assert deleted == ["network.search_top_k"]
    # reset 全部
    deleted.clear()
    cmd_network(_Args(action="reset"))
    assert "network.search_cache" in deleted and len(deleted) == len(_NET_META)


def test_cmd_network_describe_lists_all_params(monkeypatch):
    class _Cfg:
        def get(self, dotted, default=None):
            return None

    monkeypatch.setattr("qingxiaotuan.cli.cmd_network.Config",
                        lambda profile, patch_file=None: _Cfg())
    assert cmd_network(_Args(action="describe")) == 0


def test_cmd_network_set_strips_network_prefix(monkeypatch):
    written = {}

    class _Cfg:
        def get(self, dotted, default=None):
            return default

        def set_user(self, key, value):
            written[key] = value

    monkeypatch.setattr("qingxiaotuan.cli.cmd_network.Config",
                        lambda profile, patch_file=None: _Cfg())
    # 用户误写 network. 前缀也要正确落到 network.<key>
    cmd_network(_Args(action="set", key="network.search_max_results", value="350"))
    assert written == {"network.search_max_results": 350}
    cmd_network(_Args(action="set", key="search_top_k", value="5"))
    assert written["network.search_top_k"] == 5  # 字符串数字被 JSON 规整为 int


def test_cmd_network_set_requires_key_and_value(monkeypatch):
    class _Cfg:
        def get(self, dotted, default=None):
            return default

        def set_user(self, key, value):
            raise AssertionError("不应写入")

    monkeypatch.setattr("qingxiaotuan.cli.cmd_network.Config",
                        lambda profile, patch_file=None: _Cfg())
    assert cmd_network(_Args(action="set", key=None, value="1")) == 1
    assert cmd_network(_Args(action="set", key="search_top_k", value=None)) == 1