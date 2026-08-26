from datetime import datetime

from tender_parser.dedup import deduplicate_tenders
from tender_parser.models import TenderRecord


DEADLINE = datetime(2026, 6, 30, 23, 59)


def _tender(
    *,
    source: str,
    number: str | None,
    title: str = "Поставка МФУ для офиса",
    price: float = 500_000.0,
    deadline: datetime = DEADLINE,
    region: str = "Республика Крым",
    **kwargs: object,
) -> TenderRecord:
    return TenderRecord(
        title=title,
        url=f"https://example.test/{source}/{number or 'without-number'}",
        source=source,
        tender_number=number,
        region=region,
        price=price,
        deadline=deadline,
        **kwargs,
    )


def test_deduplicate_prefers_eis_and_fills_missing_fields() -> None:
    eis = _tender(source="eis-zakupki", number="EIS-1")
    aggregator = _tender(
        source="rostender",
        number="RST-1",
        title="Поставка МФУ  для офиса",
        customer="ГБУ Крыма",
        region="Крым республика",
        deadline=datetime(2026, 6, 30, 10, 0),
        raw_text="Поставка МФУ для офиса ГБУ Крыма",
    )

    result = deduplicate_tenders([aggregator, eis])

    assert result.collapsed_count == 1
    assert len(result.tenders) == 1
    assert result.tenders[0].source == "eis-zakupki"
    assert result.tenders[0].tender_number == "EIS-1"
    assert result.tenders[0].customer == "ГБУ Крыма"


def test_same_source_different_non_empty_numbers_are_never_merged() -> None:
    first = _tender(source="rostender", number="RST-1")
    second = _tender(source="rostender", number="RST-2")

    result = deduplicate_tenders([first, second])

    assert result.collapsed_count == 0
    assert {tender.tender_number for tender in result.tenders} == {"RST-1", "RST-2"}


def test_same_source_different_numbers_are_not_indirectly_merged_by_official_match() -> None:
    first = _tender(source="rostender", number="RST-1", official_number="0174100000626000005")
    second = _tender(source="rostender", number="RST-2", official_number="0174100000626000005")
    eis = _tender(source="eis-zakupki", number="0174100000626000005")

    result = deduplicate_tenders([first, second, eis])

    assert result.collapsed_count == 1
    assert len(result.tenders) == 2
    assert sorted(
        member.tender_number
        for member in result.tenders
        if member.source == "rostender"
    ) == ["RST-2"]


def test_official_number_matches_other_sources_local_number_and_keeps_provenance() -> None:
    rostender = _tender(
        source="rostender",
        number="94216089",
        title="Карточка агрегатора с другим заголовком",
        price=123.45,
        deadline=datetime(2026, 7, 2, 12, 0),
        official_number="0174100000626000005",
        official_url="https://zakupki.gov.ru/epz/order/notice/view.html?regNumber=0174100000626000005",
        official_source="eis-zakupki",
        procurement_law="44-ФЗ",
        resolution_method="rostender-meta",
        resolution_confidence=0.98,
    )
    eis = _tender(
        source="eis-zakupki",
        number="0174100000626000005",
        title="Официальное название закупки",
        resolution_confidence=0.60,
    )

    result = deduplicate_tenders([rostender, eis])

    assert result.collapsed_count == 1
    resolved = result.tenders[0]
    assert resolved.source == "eis-zakupki"
    assert resolved.tender_number == "0174100000626000005"
    assert resolved.url.endswith("/eis-zakupki/0174100000626000005")
    assert resolved.official_number == "0174100000626000005"
    assert resolved.official_source == "eis-zakupki"
    assert resolved.procurement_law == "44-ФЗ"
    assert resolved.resolution_method == "rostender-meta"
    assert resolved.resolution_confidence == 0.98


def test_official_number_can_join_multiple_distinct_sources() -> None:
    eis = _tender(source="eis-zakupki", number="32616290638")
    platform = _tender(source="etp-gpb", number="32616290638")
    rostender = _tender(
        source="rostender",
        number="94335192",
        official_number="32616290638",
        official_source="eis-zakupki",
    )

    result = deduplicate_tenders([eis, platform, rostender])

    assert result.collapsed_count == 2
    assert len(result.tenders) == 1
    assert result.tenders[0].source == "eis-zakupki"
    assert result.tenders[0].official_number == "32616290638"


def test_eis_preference_keeps_tektorg_public_contact_markers() -> None:
    eis = _tender(
        source="eis-zakupki",
        number="0174100000626000005",
        raw_text="Официальная карточка ЕИС",
    )
    platform = _tender(
        source="tektorg",
        number="TEK-101",
        official_number="0174100000626000005",
        raw_text="Карточка ТЭК-Торг\nTEKTORG_EMAIL=office@example.test",
    )

    result = deduplicate_tenders([platform, eis])

    assert len(result.tenders) == 1
    assert result.tenders[0].source == "eis-zakupki"
    assert "TEKTORG_EMAIL=office@example.test" in result.tenders[0].raw_text


def test_conflicting_official_numbers_block_exact_card_merge() -> None:
    first = _tender(
        source="eis-zakupki",
        number="EIS-1",
        official_number="0174100000626000005",
    )
    second = _tender(
        source="rostender",
        number="RST-1",
        official_number="0174100000626000006",
    )

    result = deduplicate_tenders([first, second])

    assert result.collapsed_count == 0
    assert len(result.tenders) == 2


def test_price_difference_strictly_below_one_ruble_can_merge() -> None:
    exact = _tender(source="eis-zakupki", number="EIS-1", price=100_000.0)
    rounded = _tender(source="rostender", number="RST-1", price=100_000.99)
    one_ruble = _tender(source="etp-gpb", number="GPB-1", price=100_001.0)

    result = deduplicate_tenders([rounded, one_ruble, exact])

    assert result.collapsed_count == 1
    assert len(result.tenders) == 2
    assert sorted(tender.price for tender in result.tenders if tender.price is not None) == [
        100_000.0,
        100_001.0,
    ]


def test_price_tolerance_does_not_merge_a_transitive_chain_over_one_ruble() -> None:
    low = _tender(source="rostender", number="RST-1", price=100.0)
    middle = _tender(source="eis-zakupki", number="EIS-1", price=100.75)
    high = _tender(source="roseltorg", number="ROS-1", price=101.50)

    result = deduplicate_tenders([low, middle, high])

    assert result.collapsed_count == 1
    assert len(result.tenders) == 2
    assert sorted(tender.price for tender in result.tenders if tender.price is not None) == [
        100.75,
        101.50,
    ]


def test_price_tolerance_requires_compatible_region() -> None:
    crimea = _tender(source="eis-zakupki", number="EIS-1", price=100_000.0)
    sevastopol = _tender(
        source="rostender",
        number="RST-1",
        price=100_000.50,
        region="Севастополь",
    )

    result = deduplicate_tenders([crimea, sevastopol])

    assert result.collapsed_count == 0
    assert len(result.tenders) == 2


def test_price_tolerance_does_not_enable_fuzzy_title_merge() -> None:
    short = _tender(source="eis-zakupki", number="EIS-1", title="Поставка МФУ", price=100_000.0)
    longer = _tender(
        source="rostender",
        number="RST-1",
        title="Поставка МФУ для офиса",
        price=100_000.50,
    )

    result = deduplicate_tenders([short, longer])

    assert result.collapsed_count == 0
    assert len(result.tenders) == 2
