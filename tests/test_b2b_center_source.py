from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus

from tender_parser.sources.b2b_center import (
    B2BCenterSource,
    build_search_url,
    parse_market_page,
)

SAMPLE_HTML = Path("tests/fixtures/b2b_center_market_sample.html").read_text(encoding="utf-8")


def test_build_search_url_uses_b2b_public_keyword_contract() -> None:
    decoded = unquote_plus(build_search_url("мфу"))

    assert decoded.startswith("https://www.b2b-center.ru/market/?")
    assert "f_keyword=мфу" in decoded
    assert "searching=1" in decoded


def test_parse_market_page_extracts_public_tenders() -> None:
    tenders = parse_market_page(SAMPLE_HTML, "https://www.b2b-center.ru/market/")

    assert len(tenders) == 2
    assert tenders[0].source == "b2b-center"
    assert tenders[0].tender_number == "4499001"
    assert tenders[0].title == "Поставка МФУ и принтеров для офиса"
    assert tenders[0].customer == 'ООО "Крымский заказчик"'
    assert tenders[0].price is None
    assert tenders[0].region is None
    assert tenders[0].published_at == datetime(2026, 6, 23, 10, 15)
    assert tenders[0].deadline == datetime(2026, 6, 30, 12, 0)
    assert tenders[0].url == "https://www.b2b-center.ru/market/mfu/tender-4499001/"
    assert "Офисная техника" in tenders[0].raw_text


class MarketResponse:
    text = SAMPLE_HTML

    def raise_for_status(self) -> None:
        return None


class MarketSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> MarketResponse:
        self.requested_urls.append(url)
        return MarketResponse()


def test_fetch_keywords_uses_configured_queries_and_deduplicates() -> None:
    session = MarketSession()
    source = B2BCenterSource(session=session, queries=["мфу", "принтер"])

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert len(session.requested_urls) == 2
    assert "f_keyword=" in session.requested_urls[0]
