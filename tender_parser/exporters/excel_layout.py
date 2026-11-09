"""Saved row heights for reports opened without Excel's interactive AutoFit.

OOXML has no portable 'auto-height' switch. Excel does not reliably recalculate
wrapped cells on opening, so the exporter must save a content-based height.
"""
from __future__ import annotations

import math
import re

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

EXCEL_MAX_ROW_HEIGHT = 409.5


def wrapped_line_count(text: str, width: float, font_size: float = 11) -> int:
    # Excel widths are measured in digits, not average Cyrillic glyphs. Leave
    # room for the filter arrow, word boundaries and slightly wider capitals.
    capacity = max(1, int((width - 2) * 0.88 * 11 / font_size))
    total = 0
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        lines, used = 1, 0
        for word in re.findall(r"\S+", paragraph.expandtabs(4)):
            size = len(word)
            if used and used + 1 + size > capacity:
                lines += 1
                used = 0
            elif used:
                used += 1
            # A long unbroken identifier/URL can also wrap within a word.
            full, remainder = divmod(size, capacity)
            if full:
                lines += full - (1 if remainder == 0 else 0)
                used = remainder or capacity
            else:
                used += size
        total += lines
    return max(1, total)


def _visible_text(value: object) -> str:
    text = str(value or "")
    # Count the caption of a legacy hyperlink formula, not its hidden URL.
    match = re.fullmatch(r'=HYPERLINK\("(?:[^"]|"")*",\s*"((?:[^"]|"")*)"\)', text, re.I)
    return match.group(1).replace('""', '"') if match else text


def fit_wrapped_row(sheet: Worksheet, row: int, *, minimum: float = 18,
                    columns: int | None = None) -> None:
    height = minimum
    # sheet[row] recalculates max_column by scanning the whole sheet. Avoid that
    # quadratic cost in large regional reports by passing a known column count.
    count = columns if columns is not None else sheet.max_column
    for cell in next(sheet.iter_rows(min_row=row, max_row=row, max_col=count)):
        if not cell.alignment.wrap_text or cell.value is None:
            continue
        width = sheet.column_dimensions[get_column_letter(cell.column)].width or 13
        size = cell.font.sz or 11
        lines = wrapped_line_count(_visible_text(cell.value), width, size)
        height = max(height, lines * max(15, size * 1.4) + 5)
    sheet.row_dimensions[row].height = min(EXCEL_MAX_ROW_HEIGHT, math.ceil(height))


def fit_wrapped_sheet(sheet: Worksheet) -> None:
    """Fit only wrapped cells; preserve native dates, links, filters and styles."""
    columns = sheet.max_column
    for row in range(1, sheet.max_row + 1):
        fit_wrapped_row(sheet, row, minimum=sheet.row_dimensions[row].height or 18, columns=columns)
