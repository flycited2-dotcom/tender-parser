from openpyxl import Workbook

from tender_parser.exporters.excel_layout import wrapped_line_count, fit_wrapped_row


def test_explicit_newlines_and_long_words_are_counted():
    assert wrapped_line_count("one\ntwo\n\nthree", 100) == 4
    assert wrapped_line_count("a" * 200, 20) > 5
    assert wrapped_line_count("word " * 30, 20) > 5


def test_fit_uses_font_width_and_excel_height_limit():
    from openpyxl.styles import Alignment
    sheet = Workbook().active
    sheet.column_dimensions['A'].width = 20
    sheet['A1'] = 'long text ' * 1000
    sheet['A1'].alignment = Alignment(wrap_text=True)
    fit_wrapped_row(sheet, 1)
    assert sheet.row_dimensions[1].height == 409.5
