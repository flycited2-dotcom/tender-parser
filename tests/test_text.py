from datetime import datetime

from tender_parser.text import normalize_text, parse_deadline, parse_price_rub


def test_normalize_text_lowercases_and_collapses_spaces() -> None:
    assert normalize_text("  МФУ\nПринтер   ") == "мфу принтер"


def test_parse_price_rub_accepts_russian_format() -> None:
    assert parse_price_rub("154 200,50 ₽") == 154200.50


def test_parse_price_rub_accepts_rub_with_trailing_dot() -> None:
    assert parse_price_rub("1 234.56 руб.") == 1234.56


def test_parse_price_rub_accepts_dot_thousands_and_comma_decimal() -> None:
    assert parse_price_rub("1.234,56 руб.") == 1234.56


def test_parse_price_rub_returns_none_for_missing_price() -> None:
    assert parse_price_rub("Без указания цены") is None


def test_parse_price_rub_returns_none_for_malformed_price() -> None:
    assert parse_price_rub("Цена: руб.") is None


def test_parse_deadline_reads_russian_datetime() -> None:
    assert parse_deadline("29.05.2026 10:00") == datetime(2026, 5, 29, 10, 0)
