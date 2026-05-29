from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus

import requests

from tender_parser.sources.etp_gpb import (
    EtpGpbRssSource,
    build_rss_url,
    parse_rss_feed,
)
from tender_parser.sources.rts import SourceFetchError


SAMPLE_RSS = Path("tests/fixtures/etp_gpb_rss_sample.xml").read_text(encoding="utf-8")


def test_build_rss_url_uses_actual_category_and_name_filter() -> None:
    url = build_rss_url("мфу крым")
    decoded = unquote_plus(url)

    assert url.startswith("https://etpgpb.ru/procedures.rss?")
    assert "procedure[category]=actual" in decoded
    assert "procedure[name]=мфу крым" in decoded


def test_parse_rss_feed_extracts_tenders() -> None:
    tenders = parse_rss_feed(SAMPLE_RSS, source_url="https://etpgpb.ru/procedures.rss")

    assert len(tenders) == 2
    assert tenders[0].source == "etp-gpb"
    assert tenders[0].tender_number == "123456"
    assert tenders[0].title == "Поставка многофункциональных устройств для Республики Крым"
    assert tenders[0].customer == 'ООО "Крымский заказчик"'
    assert tenders[0].region == "Республика Крым"
    assert tenders[0].price == 420_000.0
    assert tenders[0].deadline == datetime(2026, 6, 4, 10, 0)
    assert tenders[0].published_at == datetime(2026, 5, 29, 9, 30)
    assert tenders[1].customer == 'ГБУ "Севастопольский центр"'
    assert tenders[1].region == "Севастополь"
    assert tenders[1].price is None
    assert tenders[1].deadline == datetime(2026, 6, 5, 18, 0)


class RssResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class RssSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> RssResponse:
        self.requested_urls.append(url)
        return RssResponse(SAMPLE_RSS)


def test_fetch_keywords_uses_configured_queries_and_dedupes() -> None:
    session = RssSession()
    source = EtpGpbRssSource(session=session, queries=["мфу крым", "мфу севастополь"])

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert len(session.requested_urls) == 2
    assert "procedure%5Bname%5D=" in session.requested_urls[0]


class TimeoutSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> RssResponse:
        self.requested_urls.append(url)
        raise requests.Timeout("timed out")


def test_fetch_keywords_stops_after_configured_errors() -> None:
    session = TimeoutSession()
    source = EtpGpbRssSource(
        session=session,
        queries=["мфу крым", "принтер крым", "кондиционер крым"],
        max_errors=1,
    )

    try:
        source.fetch_keywords(["ignored"])
    except SourceFetchError:
        pass

    assert len(session.requested_urls) == 1
