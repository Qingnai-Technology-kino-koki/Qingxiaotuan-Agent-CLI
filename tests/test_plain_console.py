"""纯文本输出 (无高亮) 的单元测试。

覆盖:
- strip_markup: 剥离 Rich 样式标记, 保留字面 [xxx] 内容
- strip_ansi: 剥离 ANSI 转义序列
- render_plain: Markdown/Table 等 renderable 渲染为纯文本 (无 ANSI)
- PlainConsole.print: 字符串与 renderable 均输出纯文本
"""

import io
import contextlib

from rich.markdown import Markdown
from rich.table import Table

from qingxiaotuan.ui.plain_console import (
    PlainConsole,
    console,
    render_plain,
    strip_ansi,
    strip_markup,
)


def test_strip_markup_removes_rich_tags():
    assert strip_markup("[red]错误[/]") == "错误"
    assert strip_markup("[bold cyan]标题[/]") == "标题"
    assert strip_markup("[#4FA8FF]hex色[/]") == "hex色"
    assert strip_markup("正常 [dim]弱化[/] 文本") == "正常 弱化 文本"


def test_strip_markup_keeps_literal_brackets():
    # 类型注解 / 内容前缀等字面 [xxx] 不应被误伤
    assert strip_markup("Optional[int]") == "Optional[int]"
    assert strip_markup("[web_fetch] 结果") == "[web_fetch] 结果"
    assert strip_markup("a [b c] d") == "a [b c] d"


def test_strip_ansi():
    assert strip_ansi("\x1b[31m红\x1b[0m") == "红"
    assert strip_ansi("\x1b[1m加粗\x1b[0m") == "加粗"
    assert strip_ansi("纯文本") == "纯文本"


def test_render_plain_markdown_no_ansi():
    md = Markdown("# 标题\n\n**加粗** 和 *斜体*\n\n```python\nprint(1)\n```")
    out = render_plain(md)
    assert "\x1b[" not in out
    assert "标题" in out
    assert "加粗" in out
    assert "print(1)" in out


def test_render_plain_table_no_ansi():
    t = Table(title="测试表")
    t.add_column("名称")
    t.add_column("状态")
    t.add_row("引擎A", "✓")
    out = render_plain(t)
    assert "\x1b[" not in out
    assert "引擎A" in out


def test_plain_console_print_string_and_renderable():
    buf = io.StringIO()
    pc = PlainConsole(file=buf, force_terminal=False)
    pc.print("[red]错误[/]")
    pc.print(Markdown("**加粗**"))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "错误" in out
    assert "加粗" in out


def test_shared_console_is_plain():
    assert isinstance(console, PlainConsole)
