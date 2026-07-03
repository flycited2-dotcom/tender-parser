from datetime import datetime

from tender_parser.text import normalize_text, parse_deadline, parse_price_rub, word_term_matches


def test_normalize_text_lowercases_and_collapses_spaces() -> None:
    assert normalize_text("  МФУ\nПринтер   ") == "мфу принтер"


def test_normalize_text_folds_yo_and_narrow_spaces() -> None:
    assert normalize_text("Щёлкино 100 000") == "щелкино 100 000"


def test_parse_price_rub_without_currency_marker_when_not_required() -> None:
    assert parse_price_rub("1 052 860,00", require_currency=False) == 1052860.0
    assert parse_price_rub("1 052 860,00") is None


def test_word_term_matches_uses_exception_table() -> None:
    assert word_term_matches("клавиатура и мышь беспроводная", "мышь") is True
    assert word_term_matches("соединения мышьяка", "мышь") is False
    assert word_term_matches("узи щитовидной железы", "щит") is False
    assert word_term_matches("щиты распределительные", "щит") is True
    assert word_term_matches("мониторинг цен", "монитор") is False
    assert word_term_matches("поставка фенов для гостиницы", "фен") is True
    assert word_term_matches("реактив фенол чистый", "фен") is False
    assert word_term_matches("препарат феназепам", "фен") is False


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
