from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus

import pytest

from tender_parser.models import TenderRecord
from tender_parser.sources.b2b_center import (
    B2BCenterSource,
    build_search_url,
    is_blocked_page,
    is_detail_candidate,
    parse_detail_page,
    parse_market_page,
)
from tender_parser.sources.rts import SourceBlockedError

SAMPLE_HTML = Path("tests/fixtures/b2b_center_market_sample.html").read_text(encoding="utf-8")
DETAIL_HTML = Path("tests/fixtures/b2b_center_detail_sample.html").read_text(encoding="utf-8")
CAPTCHA_HTML = "<html><h1>Проверка безопасности</h1><p>Подтвердите, что вы не робот</p></html>"


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
    assert tenders[0].region == "Крым"
    assert tenders[0].published_at == datetime(2026, 6, 23, 10, 15)
    assert tenders[0].deadline == datetime(2026, 6, 30, 12, 0)
    assert tenders[0].url == "https://www.b2b-center.ru/market/mfu/tender-4499001/"
    assert "Офисная техника" in tenders[0].raw_text


def test_is_blocked_page_detects_captcha_but_prefers_real_results() -> None:
    assert is_blocked_page("https://www.b2b-center.ru/captcha/", "") is True
    assert is_blocked_page("https://www.b2b-center.ru/market/", CAPTCHA_HTML) is True
    assert is_blocked_page(
        "https://www.b2b-center.ru/market/", SAMPLE_HTML + "<!-- captcha -->"
    ) is False


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


class CaptchaResponse:
    text = CAPTCHA_HTML
    url = "https://www.b2b-center.ru/captcha/"

    def raise_for_status(self) -> None:
        return None


class CaptchaSession(MarketSession):
    def get(self, url: str, timeout: int) -> CaptchaResponse:
        self.requested_urls.append(url)
        return CaptchaResponse()


def test_fetch_with_report_marks_captcha_as_blocked() -> None:
    source = B2BCenterSource(session=CaptchaSession(), queries=["мфу"], max_details=0)

    result = source.fetch_with_report([])

    assert result.tenders == []
    assert result.health[0].source == "b2b-center"
    assert result.health[0].status == "blocked"
    assert "CAPTCHA" in result.health[0].detail
    with pytest.raises(SourceBlockedError, match="CAPTCHA"):
        source.fetch_keywords([])


class PartialCaptchaSession(MarketSession):
    def get(self, url: str, timeout: int) -> MarketResponse | CaptchaResponse:
        self.requested_urls.append(url)
        if len(self.requested_urls) == 1:
            return MarketResponse()
        return CaptchaResponse()


def test_fetch_with_report_keeps_listings_collected_before_captcha() -> None:
    source = B2BCenterSource(
        session=PartialCaptchaSession(), queries=["мфу", "принтер"], max_details=0
    )

    result = source.fetch_with_report([])

    assert len(result.tenders) == 2
    assert result.health[0].status == "partial"
    assert result.health[0].found == 2


def test_fetch_keywords_uses_configured_queries_and_deduplicates() -> None:
    session = MarketSession()
    source = B2BCenterSource(session=session, queries=["мфу", "принтер"], max_details=0)

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert len(session.requested_urls) == 2
    assert "f_keyword=" in session.requested_urls[0]


class DetailResponse:
    text = DETAIL_HTML

    def raise_for_status(self) -> None:
        return None


class DetailSession(MarketSession):
    def get(self, url: str, timeout: int) -> MarketResponse | DetailResponse:
        self.requested_urls.append(url)
        if "/tender-" in url:
            return DetailResponse()
        return MarketResponse()


class DetailCaptchaSession(MarketSession):
    def get(self, url: str, timeout: int) -> MarketResponse | CaptchaResponse:
        self.requested_urls.append(url)
        if "/tender-" in url:
            return CaptchaResponse()
        return MarketResponse()


def test_parse_detail_page_extracts_price_deadline_customer_and_address() -> None:
    detail = parse_detail_page(DETAIL_HTML)

    assert detail.price == 1_234_567.89
    assert detail.deadline == datetime(2026, 6, 30, 12, 0)
    assert detail.published_at == datetime(2026, 6, 23, 10, 15)
    assert detail.customer == 'ГБУЗ "Симферопольская больница"'
    assert "Симферополь" in (detail.delivery_address or "")


def test_is_detail_candidate_requires_category_and_missing_fields() -> None:
    interesting = TenderRecord(
        title="Поставка МФУ", url="https://example.test/1", source="b2b-center", raw_text="Поставка МФУ"
    )
    boring = TenderRecord(
        title="Поставка щебня", url="https://example.test/2", source="b2b-center", raw_text="Поставка щебня"
    )
    complete = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/3",
        source="b2b-center",
        region="Симферополь",
        price=100_000.0,
        raw_text="Поставка МФУ",
    )

    assert is_detail_candidate(interesting) is True
    assert is_detail_candidate(boring) is False
    assert is_detail_candidate(complete) is False


def test_fetch_keywords_enriches_candidates_with_detail_pages() -> None:
    session = DetailSession()
    source = B2BCenterSource(session=session, queries=["мфу"], detail_delay_seconds=0)

    tenders = source.fetch_keywords([])

    mfu = next(tender for tender in tenders if tender.tender_number == "4499001")
    assert mfu.price == 1_234_567.89
    assert mfu.region == "Симферополь"
    assert mfu.customer == 'ООО "Крымский заказчик"'
    assert mfu.detail_status == "enriched"
    assert "Симферополь" in mfu.raw_text


def test_detail_captcha_keeps_listings_and_marks_source_partial() -> None:
    source = B2BCenterSource(
        session=DetailCaptchaSession(), queries=["мфу"], detail_delay_seconds=0
    )

    result = source.fetch_with_report([])

    assert len(result.tenders) == 2
    assert result.health[0].status == "partial"
    assert "детальные карточки" in result.health[0].detail


def test_merge_detail_keeps_known_region_when_address_is_unrecognized() -> None:
    from tender_parser.sources.b2b_center import B2BDetail, _merge_detail

    tender = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/1",
        source="b2b-center",
        region="Республика Крым",
        raw_text="Поставка МФУ",
    )

    merged = _merge_detail(tender, B2BDetail(delivery_address="ул. Ленина, д. 5, стр. 2"))

    assert merged.region == "Республика Крым"


def test_empty_detail_page_does_not_mark_enriched() -> None:
    from tender_parser.sources.b2b_center import parse_detail_page

    detail = parse_detail_page("<html>Технические работы</html>")

    assert detail is None


def test_fetch_keywords_limits_detail_requests() -> None:
    session = DetailSession()
    source = B2BCenterSource(session=session, queries=["мфу"], max_details=1, detail_delay_seconds=0)

    source.fetch_keywords([])

    detail_urls = [url for url in session.requested_urls if "/tender-" in url]
    assert len(detail_urls) == 1
