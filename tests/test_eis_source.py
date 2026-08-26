from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import requests

from tender_parser.sources.eis import (
    EisZakupkiSource,
    build_regional_search_url,
    build_search_url,
    parse_search_page,
)


SAMPLE_HTML = Path("tests/fixtures/eis_search_sample.html").read_text(encoding="utf-8")


def test_build_search_url_uses_eis_public_search_filters() -> None:
    url = build_search_url("мфу крым", page=2)
    decoded = unquote(url)

    assert url.startswith("https://zakupki.gov.ru/epz/order/extendedsearch/results.html?")
    assert "searchString=мфу+крым" in decoded
    assert "pageNumber=2" in url
    assert "fz44=on" in url
    assert "fz223=on" in url
    assert "af=on" in url


def test_build_regional_search_url_uses_saved_target_regions_and_laws() -> None:
    url = build_regional_search_url(page=3)
    decoded = unquote(url)

    assert "pageNumber=3" in url
    assert "fz44=on" in url
    assert "fz223=on" in url
    assert "af=on" in url
    assert "customerPlace=OKER39,90000000000,91000000000,95000000000,92000000000" in decoded
    assert "searchString" not in url


def test_parse_search_page_extracts_eis_cards() -> None:
    tenders = parse_search_page(SAMPLE_HTML, source_url="https://zakupki.gov.ru/epz/order/extendedsearch/results.html")

    assert len(tenders) == 2
    assert tenders[0].source == "eis-zakupki"
    assert tenders[0].tender_number == "0275100000326000101"
    assert tenders[0].title == "Приобретение расходных материалов для лазерных принтеров и МФУ"
    assert tenders[0].customer == "ОТДЕЛЕНИЕ ФОНДА ПО РЕСПУБЛИКЕ КРЫМ"
    assert tenders[0].region == "Республика Крым"
    assert tenders[0].price == 1_206_241.60
    assert tenders[0].deadline == datetime(2026, 6, 4, 23, 59)
    assert tenders[0].published_at == datetime(2026, 5, 27, 0, 0)
    assert tenders[0].url.startswith("https://zakupki.gov.ru/epz/order/notice/")
    assert tenders[1].region == "Симферополь"


def test_parse_search_page_keeps_regional_card_without_visible_subject() -> None:
    html = """
    <div class="search-registry-entry-block">
      <div class="registry-entry__header-mid__number">
        <a href="/epz/order/notice/ezt20/view/common-info.html?regNumber=0374500002126000026">
          № 0374500002126000026
        </a>
      </div>
      <div class="registry-entry__body-block">
        <div class="registry-entry__body-title">Заказчик</div>
        <div class="registry-entry__body-value">ГБОУ города Севастополя</div>
      </div>
    </div>
    """

    tenders = parse_search_page(html, "https://zakupki.gov.ru/epz/order/extendedsearch/results.html")

    assert len(tenders) == 1
    assert tenders[0].title == "Закупка ЕИС № 0374500002126000026"
    assert tenders[0].customer == "ГБОУ города Севастополя"


class SearchResponse:
    def __init__(self) -> None:
        self.text = SAMPLE_HTML

    def raise_for_status(self) -> None:
        return None


class SearchSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> SearchResponse:
        self.requested_urls.append(url)
        return SearchResponse()


def test_fetch_keywords_uses_configured_queries_and_deduplicates() -> None:
    session = SearchSession()
    source = EisZakupkiSource(session=session, queries=["мфу крым", "принтер крым"])

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert len(session.requested_urls) == 2
    assert "searchString=%D0%BC%D1%84%D1%83+%D0%BA%D1%80%D1%8B%D0%BC" in session.requested_urls[0]


def test_default_source_collects_saved_regional_listing_until_empty() -> None:
    class RegionalSession(SearchSession):
        def get(self, url: str, timeout: int) -> SearchResponse:
            self.requested_urls.append(url)
            response = SearchResponse()
            if "pageNumber=2" in url:
                response.text = "<html></html>"
            return response

    session = RegionalSession()
    source = EisZakupkiSource(session=session, regional_max_pages=50)

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert len(session.requested_urls) == 2
    assert "customerPlace=OKER39%2C90000000000%2C91000000000%2C95000000000%2C92000000000" in session.requested_urls[0]
    assert "searchString" not in session.requested_urls[0]


class TimeoutSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.timeouts: list[int] = []

    def get(self, url: str, timeout: int) -> SearchResponse:
        self.timeouts.append(timeout)
        raise requests.Timeout("slow EIS")


def test_fetch_keywords_stops_after_configured_network_errors(monkeypatch) -> None:
    monkeypatch.setattr("tender_parser.http.sleep", lambda _: None)
    session = TimeoutSession()
    source = EisZakupkiSource(
        session=session,
        queries=["мфу крым", "принтер крым", "кондиционер крым"],
        timeout_seconds=7,
        max_errors=2,
    )

    try:
        source.fetch_keywords(["ignored"])
    except Exception:
        pass

    # 2 запроса до лимита ошибок, каждый с одним retry на timeout.
    assert session.timeouts == [7, 7, 7, 7]


def test_default_session_ignores_environment_proxies() -> None:
    source = EisZakupkiSource(queries=["мфу крым"])

    assert source.session.trust_env is False
