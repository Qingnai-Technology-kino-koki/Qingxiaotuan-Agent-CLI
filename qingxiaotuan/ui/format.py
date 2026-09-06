"""轻量纯文本表格格式化器 —— 替代 rich.Table，零外部依赖。

用法:
    t = Table(title="定时任务")
    t.add_column("ID", justify="right")
    t.add_column("名称")
    t.add_row("abc", "每日总结")
    print(t)
"""

from __future__ import annotations

from typing import List, Optional


class Table:
    """极简 ASCII 表格，支持列对齐与可选标题。"""

    def __init__(
        self,
        title: str = "",
        show_header: bool = True,
        **_kwargs: object,
    ) -> None:
        self._title = title
        self._show_header = show_header
        self._columns: List[dict] = []
        self._rows: List[List[str]] = []

    def add_column(self, name: str, justify: str = "left", **_kw: object) -> None:
        self._columns.append({"name": name, "justify": justify})

    def add_row(self, *cells: str) -> None:
        self._rows.append([str(c) for c in cells])

    def _col_widths(self) -> List[int]:
        widths: List[int] = []
        for i, col in enumerate(self._columns):
            header_len = len(col["name"]) if self._show_header else 0
            data_max = max((len(row[i]) if i < len(row) else 0) for row in self._rows) if self._rows else 0
            widths.append(max(header_len, data_max))
        return widths

    def _fmt_cell(self, text: str, width: int, justify: str) -> str:
        if justify == "right":
            return text.rjust(width)
        if justify == "center":
            return text.center(width)
        return text.ljust(width)

    def __str__(self) -> str:
        if not self._columns:
            return self._title or ""

        widths = self._col_widths()
        sep = "  ".join("-" * w for w in widths)
        lines: List[str] = []

        if self._title:
            lines.append(self._title)

        if self._show_header and self._columns:
            header = "  ".join(
                self._fmt_cell(col["name"], widths[i], col["justify"])
                for i, col in enumerate(self._columns)
            )
            lines.append(header)
            lines.append(sep)

        for row in self._rows:
            cells = []
            for i, col in enumerate(self._columns):
                val = row[i] if i < len(row) else ""
                cells.append(self._fmt_cell(val, widths[i], col["justify"]))
            lines.append("  ".join(cells))

        return "\n".join(lines)
