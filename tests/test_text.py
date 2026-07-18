from datetime import datetime

from tender_parser.text import (
    normalize_text,
    parse_deadline,
    parse_price_rub,
    phrase_stems_match,
    word_term_matches,
)


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


def test_word_term_matches_does_not_treat_provoditsya_as_wire() -> None:
    assert word_term_matches("аукцион проводится в электронной форме", "провод") is False
    assert word_term_matches("работы проводились в мае", "провод") is False
    assert word_term_matches("поставка проводов и кабеля", "провод") is True
    assert word_term_matches("монтаж электропроводки, проводник медный", "проводник") is True


def test_word_term_matches_covers_oblique_cases_via_stem() -> None:
    assert word_term_matches("поставка бумаги офисной формата а4", "бумага") is True
    assert word_term_matches("закупка посуды столовой", "посуда") is True
    assert word_term_matches("поставка ламп светодиодных", "лампа") is True
    assert word_term_matches("установка розетки двойной", "розетка") is True
    # Беглая гласная («розеток») стем-fallback не покрывает — известное ограничение.
    assert word_term_matches("замена розеток", "розетка") is False


def test_word_term_matches_blocks_fentanyl_and_fennel() -> None:
    assert word_term_matches("поставка фентанила", "фен") is False
    assert word_term_matches("фенхель сушеный", "фен") is False
    assert word_term_matches("поставка фенов для гостиницы", "фен") is True


def test_word_term_matches_blocks_uso_false_stems() -> None:
    assert word_term_matches("услуги по погребению усопших", "усо") is False
    assert word_term_matches("работы по усовершенствованию системы", "усо") is False
    assert word_term_matches("стойка усо в комплекте", "усо") is True


def test_parse_price_rub_understands_millions_and_thousands() -> None:
    assert parse_price_rub("1,2 млн руб") == 1_200_000.0
    assert parse_price_rub("450 тыс. руб") == 450_000.0


def test_phrase_stems_match_covers_grammatical_cases() -> None:
    assert phrase_stems_match("поставка хозяйственных товаров", "хозяйственные товары") is True
    assert phrase_stems_match("поставка сетевого оборудования", "сетевое оборудование") is True


def test_phrase_stems_match_requires_word_boundaries() -> None:
    text = "департамент административно-хозяйственного обеспечения закупает товары"
    assert phrase_stems_match(text, "хозяйственные товары") is False


def test_phrase_stems_match_limits_gap_between_words() -> None:
    scattered = "поставка и монтаж кондиционеров, ремонт декоративных кронштейнов на фасаде здания"
    assert phrase_stems_match(scattered, "ремонт здания") is False
    close = "выполнение капитального ремонта здания школы"
    assert phrase_stems_match(close, "ремонт здания") is True


def test_parse_deadline_reads_russian_datetime() -> None:
    assert parse_deadline("29.05.2026 10:00") == datetime(2026, 5, 29, 10, 0)
