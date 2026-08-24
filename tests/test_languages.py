import json

from qingxiaotuan.core.kernel import Kernel
from qingxiaotuan.tools.base import ToolContext, ToolRegistry
from qingxiaotuan.tools.languages import LanguagePlugin, project_languages, language_checks


def test_project_languages_detects_python_and_typescript(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"typecheck": "tsc", "test": "vitest"}}), encoding="utf-8"
    )
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    context = ToolContext(kernel=Kernel(), workspace=str(tmp_path))
    result = json.loads(project_languages(context))
    assert {item["name"] for item in result["languages"]} == {"python", "typescript"}


def test_language_checks_rejects_unknown_language(tmp_path):
    context = ToolContext(kernel=Kernel(), workspace=str(tmp_path))
    assert "未识别" in language_checks(context)


def test_language_plugin_registers_tools():
    kernel = Kernel()
    kernel.provide("tool_registry", ToolRegistry())
    LanguagePlugin().activate(kernel)
    assert kernel.require("tool_registry").get("project_languages") is not None
    assert kernel.require("tool_registry").get("language_checks") is not None