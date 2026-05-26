from pathlib import Path

from tender_parser.sources.rts import parse_market_page


def test_parse_market_page_extracts_table_rows() -> None:
    html = Path("tests/fixtures/rts_market_sample.html").read_text(encoding="utf-8")
    tenders = parse_market_page(html, source_url="https://www.rosatom.rts-tender.ru/market/")

    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.tender_number == "4455001"
    assert tender.title == "Запрос предложений № 4455001 Поставка МФУ в Республику Крым"
    assert tender.customer == 'АО "ТЕСТ"'
    assert tender.price == 45_000.0
    assert tender.deadline.year == 2026
