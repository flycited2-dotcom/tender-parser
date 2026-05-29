from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from tender_parser.sources.rostender import (
    RostenderSource,
    build_search_url,
    parse_search_page,
)


SAMPLE_HTML = Path("tests/fixtures/rostender_search_sample.html").read_text(encoding="utf-8")


def test_build_search_url_uses_keywords_and_open_filters() -> None:
    url = build_search_url("мфу крым")

    decoded = unquote(url)
    assert url.startswith("https://rostender.info/extsearch?")
    assert "keywords=мфу+крым" in decoded
    assert "open=1" in url
    assert "default_search=0" in url


def test_parse_search_page_extracts_tenders() -> None:
    tenders = parse_search_page(SAMPLE_HTML, source_url="https://rostender.info/extsearch")

    assert len(tenders) == 2
    assert tenders[0].source == "rostender"
    assert tenders[0].tender_number == "92098187"
    assert tenders[0].title == "поставка многофункциональных устройств (МФУ)"
    assert tenders[0].region == "Крым республика"
    assert tenders[0].price == 1_350_304.0
    assert tenders[0].deadline == datetime(2026, 5, 29, 23, 59)
    assert tenders[0].url.startswith("https://rostender.info/region/krym-respublika/")
    assert tenders[1].price is None
    assert tenders[1].deadline == datetime(2026, 6, 4, 10, 0)


class SearchResponse:
    def __init__(self, url: str) -> None:
        self.url = url
        self.text = SAMPLE_HTML

    def raise_for_status(self) -> None:
        return None


class SearchSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> SearchResponse:
        self.requested_urls.append(url)
        return SearchResponse(url)


def test_fetch_keywords_uses_configured_queries() -> None:
    session = SearchSession()
    source = RostenderSource(session=session, queries=["мфу крым"])

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert "keywords=%D0%BC%D1%84%D1%83+%D0%BA%D1%80%D1%8B%D0%BC" in session.requested_urls[0]
