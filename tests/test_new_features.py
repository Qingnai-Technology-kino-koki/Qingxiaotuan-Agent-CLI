"""阶段 C 新能力测试: 工具缓存 / 配置校验 / 会话管理。"""

import json

from qingxiaotuan.core.kernel import Kernel
from qingxiaotuan.tools import ToolRegistryPlugin, builtin_tool_plugins
from qingxiaotuan.tools.base import ToolContext
from qingxiaotuan.tools.cache import ToolResultCache
from qingxiaotuan.config import Config
from qingxiaotuan.config.validate import validate


def _kernel_with_cache():
    k = Kernel()
    k.register(ToolRegistryPlugin())
    for p in builtin_tool_plugins():
        if p.name in ("tools.filesystem", "tools.web"):
            k.register(p)
    k.activate_all()
    return k


def test_cache_hits_read_and_invalidates_on_write(tmp_path):
    k = _kernel_with_cache()
    registry = k.require("tool_registry")
    ctx = ToolContext(kernel=k, workspace=str(tmp_path), confirm=lambda _p: True)

    # 写 + 读 (读进缓存)
    registry.dispatch("write_file", json.dumps({"path": "a.txt", "content": "v1"}), ctx)
    r1 = registry.dispatch("read_file", json.dumps({"path": "a.txt"}), ctx)
    assert "v1" in r1 and "[缓存]" not in r1
    # 再次读应命中缓存
    r2 = registry.dispatch("read_file", json.dumps({"path": "a.txt"}), ctx)
    assert "[缓存]" in r2
    # 写 (危险) 应清空缓存, 随后读为最新值
    registry.dispatch("write_file", json.dumps({"path": "a.txt", "content": "v2"}), ctx)
    r3 = registry.dispatch("read_file", json.dumps({"path": "a.txt"}), ctx)
    assert "v2" in r3 and "[缓存]" not in r3


def test_cache_disabled_when_ttl_zero(tmp_path):
    cache = ToolResultCache(ttl=0.0)
    k = Kernel()
    from qingxiaotuan.tools.base import ToolRegistry
    reg = ToolRegistry(cache=cache)
    # 直接用注册表: 写两次同参数, ttl=0 不应命中
    reg.register(__import__("qingxiaotuan.tools.filesystem", fromlist=["FilesystemPlugin"]).FilesystemPlugin())
    # 简化: 仅验证 ToolResultCache 在 ttl=0 时不存储
    cache.put("t", "{}", "x", dangerous=False)
    assert cache.get("t", "{}") is None


def test_config_validate_reports_type_error(qxt_home):
    cfg = Config()
    cfg.set_user("model.temperature", "not-a-number")  # 存成字符串
    errors, _ = validate(cfg)
    assert any("model.temperature" in e for e in errors)


def test_config_validate_passes_clean_default(qxt_home):
    cfg = Config()
    errors, _ = validate(cfg)
    # 默认配置应无阻断性错误 (可能有 api key 缺失的 warning, 不影响)
    assert errors == []


def test_session_list_empty_when_no_sessions(tmp_path, qxt_home):
    from qingxiaotuan.memory import SessionStore
    store = SessionStore(qxt_home)
    assert store.list_sessions() == []
