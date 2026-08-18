from pathlib import Path
from urllib.parse import unquote, unquote_plus

import pytest

from tender_parser.config import RTS_SEARCH_QUERIES, RTS_TIMEOUT_SECONDS
from tender_parser.sources.rts import (
    RtsMarketEndpoint,
    RtsPublicSource,
    SourceBlockedError,
    SourceFetchError,
    build_search_url,
    parse_market_page,
)


SAMPLE_HTML = Path("tests/fixtures/rts_market_sample.html").read_text(encoding="utf-8")
EMPTY_HTML = "<html><body><table class='search-results'><tbody></tbody></table></body></html>"


def test_parse_market_page_extracts_table_rows() -> None:
    tenders = parse_market_page(SAMPLE_HTML, source_url="https://www.rosatom.rts-tender.ru/market/")

    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.tender_number == "4455001"
    assert tender.title == "Запрос предложений № 4455001 Поставка МФУ в Республику Крым"
    assert tender.customer == 'АО "ТЕСТ"'
    assert tender.price == 45_000.0
    assert tender.deadline.year == 2026


def test_build_search_url_accepts_custom_base_url() -> None:
    url = build_search_url(
        "МФУ",
        page_index=1,
        base_url="https://zakupki-simferopol.rts-tender.ru/market/",
    )

    assert url.startswith("https://zakupki-simferopol.rts-tender.ru/market/?")
    assert "f_keyword=" in url
    assert "МФУ" in unquote(url)
    assert "from=20" in url


def test_parse_market_page_applies_source_and_region_hint() -> None:
    tenders = parse_market_page(
        SAMPLE_HTML,
        source_url="https://zakupki-simferopol.rts-tender.ru/market/",
        source_name="rts-zakupki-simferopol",
        region_hint="Симферополь",
    )

    assert tenders[0].source == "rts-zakupki-simferopol"
    assert tenders[0].region == "Симферополь"


class CaptchaResponse:
    url = "https://www.rosatom.rts-tender.ru/captcha/?url=%2Fmarket%2F"
    text = "Превышен максимальный лимит скорости просмотра страниц. Регламент площадки не допускает использование ботов."

    def raise_for_status(self) -> None:
        return None


class CaptchaSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: int) -> CaptchaResponse:
        return CaptchaResponse()


class MarketResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text

    def raise_for_status(self) -> None:
        return None


class MultiEndpointSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> MarketResponse:
        self.requested_urls.append(url)
        if "two.rts-tender.ru" in url:
            html = SAMPLE_HTML.replace("4455001", "4455002").replace(
                "Поставка МФУ в Республику Крым",
                "Поставка принтера",
            )
            return MarketResponse(url, html)
        return MarketResponse(url, SAMPLE_HTML)


class MixedEndpointSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: int) -> MarketResponse | CaptchaResponse:
        if "blocked.rts-tender.ru" in url:
            return CaptchaResponse()
        return MarketResponse(url, SAMPLE_HTML)


class PartialEndpointSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls = 0

    def get(self, url: str, timeout: int) -> MarketResponse | CaptchaResponse:
        self.calls += 1
        if self.calls == 1:
            return MarketResponse(url, SAMPLE_HTML)
        if self.calls == 2:
            return MarketResponse(url, EMPTY_HTML)
        return CaptchaResponse()


class TimeoutCaptureSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.timeout: int | None = None

    def get(self, url: str, timeout: int) -> MarketResponse:
        self.timeout = timeout
        return MarketResponse(url, SAMPLE_HTML)


class QueryCaptureSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> MarketResponse:
        self.requested_urls.append(url)
        return MarketResponse(url, EMPTY_HTML)


class SharedTenderSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: int) -> MarketResponse:
        return MarketResponse(url, SAMPLE_HTML)


def test_fetch_with_report_visits_region_hinted_endpoints_first() -> None:
    source = RtsPublicSource(
        session=SharedTenderSession(),
        endpoints=[
            RtsMarketEndpoint("https://www.rosatom.rts-tender.ru/market/", "rts-rosatom"),
            RtsMarketEndpoint(
                "https://zakupki-simferopol.rts-tender.ru/market/",
                "rts-zakupki-simferopol",
                "Симферополь",
            ),
        ],
        queries=["мфу"],
    )

    result = source.fetch_with_report(["мфу"])

    kept = [tender for tender in result.tenders if tender.tender_number == "4455001"]
    assert kept
    assert kept[0].region == "Симферополь"
    assert kept[0].source == "rts-zakupki-simferopol"


def test_fetch_keyword_raises_when_source_returns_captcha() -> None:
    source = RtsPublicSource(session=CaptchaSession())

    with pytest.raises(SourceBlockedError, match="captcha"):
        source.fetch_keyword("компьютер")


def test_fetch_keyword_uses_rts_timeout() -> None:
    session = TimeoutCaptureSession()
    source = RtsPublicSource(session=session)

    source.fetch_keyword("МФУ")

    assert session.timeout == RTS_TIMEOUT_SECONDS


def test_fetch_with_report_uses_rts_query_list_by_default() -> None:
    session = QueryCaptureSession()
    source = RtsPublicSource(
        session=session,
        endpoints=[RtsMarketEndpoint("https://one.rts-tender.ru/market/", "rts-one")],
        query_delay_seconds=0,
    )

    source.fetch_with_report(["Симферополь"])

    first_url = unquote_plus(session.requested_urls[0])
    assert RTS_SEARCH_QUERIES[0] in first_url
    assert "Симферополь" not in first_url


def test_fetch_keywords_queries_all_configured_endpoints() -> None:
    session = MultiEndpointSession()
    source = RtsPublicSource(
        session=session,
        endpoints=[
            RtsMarketEndpoint("https://one.rts-tender.ru/market/", "rts-one"),
            RtsMarketEndpoint("https://two.rts-tender.ru/market/", "rts-two", "Симферополь"),
        ],
        query_delay_seconds=0,
    )

    tenders = source.fetch_keywords(["МФУ"])

    assert len(tenders) == 2
    assert {tender.source for tender in tenders} == {"rts-one", "rts-two"}
    assert any(tender.region == "Симферополь" for tender in tenders)
    assert any("one.rts-tender.ru" in url for url in session.requested_urls)
    assert any("two.rts-tender.ru" in url for url in session.requested_urls)


def test_fetch_with_report_returns_endpoint_health_when_one_endpoint_is_blocked() -> None:
    source = RtsPublicSource(
        session=MixedEndpointSession(),
        endpoints=[
            RtsMarketEndpoint("https://blocked.rts-tender.ru/market/", "rts-blocked"),
            RtsMarketEndpoint("https://open.rts-tender.ru/market/", "rts-open"),
        ],
        query_delay_seconds=0,
    )

    result = source.fetch_with_report(["МФУ"])

    assert len(result.tenders) == 1
    assert [(item.source, item.status, item.found) for item in result.health] == [
        ("rts-blocked", "blocked", 0),
        ("rts-open", "ok", 1),
    ]
    assert "rts-blocked" in result.errors[0]


def test_fetch_with_report_marks_partial_endpoint_and_measures_elapsed(monkeypatch) -> None:
    times = iter([10.0, 12.5])
    monkeypatch.setattr("tender_parser.sources.rts.monotonic", lambda: next(times))
    source = RtsPublicSource(
        session=PartialEndpointSession(),
        endpoints=[
            RtsMarketEndpoint("https://partial.rts-tender.ru/market/", "rts-partial"),
        ],
    )

    result = source.fetch_with_report(["МФУ", "принтер"])

    assert len(result.tenders) == 1
    assert [(item.source, item.status, item.found, item.elapsed_seconds) for item in result.health] == [
        ("rts-partial", "partial", 1, 2.5)
    ]
    assert "captcha" in result.errors[0]


def test_fetch_keywords_raises_fetch_error_when_every_endpoint_is_blocked() -> None:
    source = RtsPublicSource(
        session=CaptchaSession(),
        endpoints=[
            RtsMarketEndpoint("https://one.rts-tender.ru/market/", "rts-one"),
            RtsMarketEndpoint("https://two.rts-tender.ru/market/", "rts-two"),
        ],
    )

    with pytest.raises(SourceFetchError, match="все источники RTS"):
        source.fetch_keywords(["МФУ", "принтер"])
