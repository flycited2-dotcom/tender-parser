from datetime import datetime

from tender_parser.dedup import deduplicate_tenders
from tender_parser.models import TenderRecord


def test_deduplicate_prefers_eis_and_fills_missing_fields() -> None:
    eis = TenderRecord(
        title="Поставка МФУ для офиса",
        url="https://zakupki.gov.ru/notice/1",
        source="eis-zakupki",
        tender_number="EIS-1",
        region="Республика Крым",
        price=500_000.0,
        deadline=datetime(2026, 6, 30, 23, 59),
    )
    aggregator = TenderRecord(
        title="Поставка МФУ  для офиса",
        url="https://rostender.info/tender/1",
        source="rostender",
        tender_number="RST-1",
        customer="ГБУ Крыма",
        region="Крым республика",
        price=500_000.0,
        deadline=datetime(2026, 6, 30, 10, 0),
        raw_text="Поставка МФУ для офиса ГБУ Крыма",
    )

    result = deduplicate_tenders([aggregator, eis])

    assert result.collapsed_count == 1
    assert len(result.tenders) == 1
    assert result.tenders[0].source == "eis-zakupki"
    assert result.tenders[0].tender_number == "EIS-1"
    assert result.tenders[0].customer == "ГБУ Крыма"
