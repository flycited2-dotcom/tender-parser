from datetime import datetime
from pathlib import Path

from tender_parser.browser.rts_watcher import collect_from_page
from tender_parser.rts_accumulator import RtsAccumulator
from tender_parser.sources.rts_poisk import parse_poisk_page

SAMPLE_HTML = Path("tests/fixtures/rts_poisk_sample.html").read_text(encoding="utf-8")
POISK_URL = "https://www.rts-tender.ru/poisk/search?id=0926554c"


def test_parse_poisk_page_extracts_cards() -> None:
    tenders = parse_poisk_page(SAMPLE_HTML, POISK_URL)

    assert len(tenders) == 2
    first = tenders[0]
    assert first.source == "rts-poisk"
    assert first.tender_number == "100018248126100076"
    assert first.title == "Поставка МФУ для нужд Крымской таможни"
    assert first.url == "https://agregatoreat.ru/purchases/announcement/bd916aa6/info"
    assert first.price == 184_400.0
    assert first.deadline == datetime(2026, 7, 6, 12, 36)
    assert first.published_at == datetime(2026, 7, 3, 12, 37, 3)
    assert first.customer == "КРЫМСКАЯ ТАМОЖНЯ"
    assert first.status == "Прием заявок"
    assert first.region == "Крым"
    assert "ЕАТ" in first.raw_text
    assert "26.20.18" in first.raw_text


def test_parse_poisk_page_handles_missing_price_and_detects_region() -> None:
    tenders = parse_poisk_page(SAMPLE_HTML, POISK_URL)

    second = tenders[1]
    assert second.tender_number == "РТС-555/26"
    assert second.price is None
    assert second.deadline == datetime(2026, 7, 10, 10, 0)
    assert second.customer == "ГУП Севастопольэнерго"
    assert second.region == "Севастополь"


def test_parse_poisk_page_returns_empty_for_other_pages() -> None:
    assert parse_poisk_page("<html><body>ничего</body></html>", POISK_URL) == []


def test_collect_from_page_accumulates_poisk_results(tmp_path: Path) -> None:
    accumulator = RtsAccumulator(tmp_path / "tenders.db")

    result = collect_from_page(SAMPLE_HTML, POISK_URL, accumulator)

    assert result is not None
    added, total = result
    assert added == 2
    records = accumulator.load_all()
    assert {record.source for record in records} == {"rts-poisk"}
