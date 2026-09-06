"""Task 4 验证: 入口可解析 + openai SDK 懒加载 (仅在真正使用 OpenAI 传输层时导入)。

- pyproject 入口 qingxiaotuan.cli:main 经 qingxiaotuan/cli/__init__.py 转发到 .parser.main;
- 导入 models.openai_compat 不应在模块加载时触碰 openai SDK (核心逻辑不依赖它);
- 即使未安装 openai, 非 OpenAI 场景 (如本地/Ollama) 也能加载该模块。
"""

from __future__ import annotations

import importlib
import sys


def test_cli_entry_point_resolves():
    """qingxiaotuan.cli:main 可解析且为可调用 (对照 pyproject 的 [project.scripts]/入口)。"""
    import qingxiaotuan.cli as cli_pkg
    assert hasattr(cli_pkg, "main"), "qingxiaotuan.cli 应暴露 main (来自 .parser)"
    assert callable(cli_pkg.main), "main 必须可调用"


def test_openai_sdk_is_lazy_at_module_load():
    """导入 models.openai_compat 不应在加载时导入 openai (SDK 为可选传输层依赖)。"""
    before = set(sys.modules)
    mod = importlib.import_module("qingxiaotuan.models.openai_compat")
    after = set(sys.modules)
    # 模块顶层不应直接持有 OpenAI 符号 (只在 client 属性内延迟导入)
    assert "OpenAI" not in mod.__dict__, "openai 不应在模块加载时导入"
    # 导入本模块本身不应主动把 openai 拉进 sys.modules
    assert "openai" not in (after - before), "openai 不应因导入本模块而被加载"


def test_openai_adapter_importable_without_sdk():
    """OpenAICompatAdapter 类本身可导入 (类定义不依赖 openai 运行时)。"""
    from qingxiaotuan.models.openai_compat import OpenAICompatAdapter
    assert OpenAICompatAdapter is not None
    # 未配置 key 时, client 属性应给出友好报错而非静默 None
    inst = OpenAICompatAdapter(base_url="http://x/v1", model="m", api_key=None)
    try:
        inst.client
        raise AssertionError("无 key 应抛 RuntimeError")
    except RuntimeError as exc:
        assert "API Key" in str(exc)
