from datetime import datetime

from tender_parser.filters import evaluate_tender
from tender_parser.models import TenderRecord


NOW = datetime(2026, 5, 19, 12, 0)


def make_tender(**overrides: object) -> TenderRecord:
    data = {
        "title": "Поставка МФУ в Республику Крым",
        "url": "https://example.test/tender-1/",
        "source": "test",
        "tender_number": "1",
        "customer": "Заказчик",
        "region": "Республика Крым",
        "price": 45_000.0,
        "deadline": datetime(2026, 5, 25, 10, 0),
        "raw_text": "Поставка МФУ в Республику Крым",
    }
    data.update(overrides)
    return TenderRecord(**data)


def test_evaluate_tender_matches_region_category_price_and_deadline() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.category == "Компьютерная техника и периферия"
    assert "регион: республика крым" in result.include_reason.lower()
    assert "мфу" in result.include_reason.lower()


def test_evaluate_tender_excludes_stop_terms() -> None:
    result = evaluate_tender(make_tender(title="Поставка лекарственных препаратов МФУ"), now=NOW)

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason
    assert "лекарственные препараты" in result.exclude_reason


def test_evaluate_tender_excludes_low_price() -> None:
    result = evaluate_tender(make_tender(price=29_999.0), now=NOW)

    assert result.filter_status == "excluded"
    assert "меньше 30000" in result.exclude_reason


def test_evaluate_tender_excludes_expired_deadline() -> None:
    result = evaluate_tender(make_tender(deadline=datetime(2026, 5, 18, 23, 59)), now=NOW)

    assert result.filter_status == "excluded"
    assert "срок подачи истек" in result.exclude_reason


def test_evaluate_tender_excludes_missing_region() -> None:
    result = evaluate_tender(make_tender(region="Москва", raw_text="Поставка МФУ"), now=NOW)

    assert result.filter_status == "excluded"
    assert "регион не найден" in result.exclude_reason


def test_evaluate_tender_clears_matched_metadata_when_excluded() -> None:
    enriched = make_tender(
        price=29_999.0,
        category="Компьютерная техника и периферия",
        include_reason="old include",
        matched_terms=["мфу"],
    )

    result = evaluate_tender(enriched, now=NOW)

    assert result.filter_status == "excluded"
    assert result.category is None
    assert result.include_reason == ""
    assert result.matched_terms == []
