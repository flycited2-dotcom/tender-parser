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


def test_evaluate_tender_marks_verified_card_exact() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.match_confidence == "точное"


def test_evaluate_tender_marks_exact_match_hot() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.review_priority == "hot"


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


def test_evaluate_tender_reviews_missing_region_for_interesting_category() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка МФУ", region=None, raw_text="Поставка МФУ"),
        now=NOW,
    )

    assert result.filter_status == "review"
    assert result.category == "Компьютерная техника и периферия"
    assert result.match_confidence == "ручная проверка"
    assert "регион не найден" in result.exclude_reason


def test_evaluate_tender_reviews_unknown_deadline_as_probable() -> None:
    result = evaluate_tender(make_tender(deadline=None), now=NOW)

    assert result.filter_status == "review"
    assert result.match_confidence == "вероятное"
    assert result.review_priority == "review"
    assert result.exclude_reason == "требуется проверка: срок подачи не указан"


def test_evaluate_tender_marks_missing_deadline_as_review_priority() -> None:
    result = evaluate_tender(make_tender(deadline=None), now=NOW)

    assert result.filter_status == "review"
    assert result.match_confidence == "вероятное"
    assert result.review_priority == "review"


def test_evaluate_tender_marks_b2b_missing_region_and_price_as_wide() -> None:
    result = evaluate_tender(
        make_tender(
            source="b2b-center",
            title="Поставка кондиционеров",
            region=None,
            price=None,
            raw_text="Поставка кондиционеров",
        ),
        now=NOW,
    )

    assert result.filter_status == "review"
    assert result.match_confidence == "ручная проверка"
    assert result.review_priority == "wide"


def test_evaluate_tender_excludes_known_non_target_region() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка сейфов", region="Москва", raw_text="Поставка сейфов"),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.exclude_reason == "регион не целевой"


def test_evaluate_tender_reviews_missing_price_for_interesting_category_and_region() -> None:
    result = evaluate_tender(make_tender(price=None), now=NOW)

    assert result.filter_status == "review"
    assert result.category == "Компьютерная техника и периферия"
    assert "сумма не указана" in result.exclude_reason


def test_evaluate_tender_does_not_treat_monitoring_as_monitor() -> None:
    result = evaluate_tender(
        make_tender(
            title="Мониторинг рынка цен в Республике Крым",
            raw_text="Мониторинг рынка цен в Республике Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "категория интереса не найдена" in result.exclude_reason


def test_evaluate_tender_does_not_treat_pipeline_as_wire() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка трубопроводов из стали в Республику Крым",
            raw_text="Поставка трубопроводов из стали в Республику Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "категория интереса не найдена" in result.exclude_reason


def test_evaluate_tender_excludes_filter_cartridges() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка фильтровальных картриджей в Республику Крым",
            raw_text="Поставка фильтровальных картриджей в Республику Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_excludes_medical_bicarbonate_cartridges() -> None:
    result = evaluate_tender(
        make_tender(
            title="Картриджи бикарбонатные и концентраты кислотные",
            raw_text="Картриджи бикарбонатные и концентраты кислотные в Республику Крым",
            region="Крым",
            price=1_500_000.0,
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_excludes_lab_consumables_even_with_office_word() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка расходных материалов для клинико-диагностической лаборатории",
            raw_text="Поставка ручек-скарификаторов и расходных материалов для лаборатории в Севастополь",
            region="Севастополь",
            price=500_000.0,
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.review_priority == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_excludes_generic_consumables_without_target_context() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка расходных материалов",
            raw_text="Поставка расходных материалов в Республику Крым",
            region="Республика Крым",
            price=120_000.0,
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.review_priority == "excluded"


def test_evaluate_tender_matches_actual_monitor_word() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка мониторов в Республику Крым",
            raw_text="Поставка мониторов в Республику Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Компьютерная техника и периферия"
    assert "монитор" in result.include_reason.lower()


def test_evaluate_tender_matches_region_from_title() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка МФУ в Севастополь", region=None, raw_text="Поставка МФУ"),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert "регион: севастополь" in result.include_reason.lower()


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
