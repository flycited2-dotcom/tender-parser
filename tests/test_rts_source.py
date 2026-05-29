from pathlib import Path
from urllib.parse import unquote

import pytest

from tender_parser.sources.rts import (
    RtsMarketEndpoint,
    RtsPublicSource,
    SourceBlockedError,
    SourceFetchError,
    build_search_url,
    parse_market_page,
)


SAMPLE_HTML = Path("tests/fixtures/rts_market_sample.html").read_text(encoding="utf-8")


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


def test_fetch_keyword_raises_when_source_returns_captcha() -> None:
    source = RtsPublicSource(session=CaptchaSession())

    with pytest.raises(SourceBlockedError, match="captcha"):
        source.fetch_keyword("компьютер")


def test_fetch_keywords_queries_all_configured_endpoints() -> None:
    session = MultiEndpointSession()
    source = RtsPublicSource(
        session=session,
        endpoints=[
            RtsMarketEndpoint("https://one.rts-tender.ru/market/", "rts-one"),
            RtsMarketEndpoint("https://two.rts-tender.ru/market/", "rts-two", "Симферополь"),
        ],
    )

    tenders = source.fetch_keywords(["МФУ"])

    assert len(tenders) == 2
    assert {tender.source for tender in tenders} == {"rts-one", "rts-two"}
    assert any(tender.region == "Симферополь" for tender in tenders)
    assert any("one.rts-tender.ru" in url for url in session.requested_urls)
    assert any("two.rts-tender.ru" in url for url in session.requested_urls)


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
