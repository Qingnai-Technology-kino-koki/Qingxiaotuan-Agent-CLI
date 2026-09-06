"""run_shell 纳入影响半径 (事前可见化) 的回归测试。

修复前: _MUTATION_EXTRACTORS 仅覆盖 5 个文件工具, run_shell 拿不到影响预览,
「最小影响半径」对 shell 是空头支票。修复后: run_shell 的命令会作为影响目标呈现,
且明确标注「不可回滚」 (因为快照对命令字符串安全降级, 不会伪造回滚)。
"""

from qingxiaotuan.tools.base import (
    _MUTATION_EXTRACTORS,
    _build_impact_preview,
)


def test_run_shell_extractor_returns_command():
    ext = _MUTATION_EXTRACTORS["run_shell"]
    assert ext({"command": "rm -rf /tmp/x"}, None) == ["rm -rf /tmp/x"]
    assert ext({}, None) == []  # 无 command 时空 (不误报)


def test_run_shell_preview_labels_non_rollbackable():
    preview = _build_impact_preview(
        "run_shell", {"command": "rm -rf /tmp/x"}, None, ["rm -rf /tmp/x"]
    )
    assert "执行命令" in preview
    assert "不可回滚" in preview
    assert "rm -rf /tmp/x" in preview


def test_file_tools_still_extracted():
    # 回归: 文件工具不受影响
    assert _MUTATION_EXTRACTORS["write_file"]({"path": "a.py"}, None) == ["a.py"]
    assert _MUTATION_EXTRACTORS["move_file"](
        {"src": "a", "dst": "b"}, None
    ) == ["a", "b"]
