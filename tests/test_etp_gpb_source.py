from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus

import requests

from tender_parser.sources.etp_gpb import (
    EtpGpbApiSource,
    build_api_url,
    build_rss_url,
    parse_api_payload,
    parse_rss_feed,
)
from tender_parser.sources.rts import SourceFetchError


SAMPLE_RSS = Path("tests/fixtures/etp_gpb_rss_sample.xml").read_text(encoding="utf-8")

SAMPLE_API = {
    "data": [
        {
            "id": "223-id",
            "type": "procedure",
            "attributes": {
                "registry_number": "32616251574",
                "title": "Монтаж кнопки тревожной сигнализации",
                "company_name": 'МАУ "ЦКТМО"',
                "amount": "2396777.53",
                "currency_name": "RUB",
                "lot_regions": ["Тюменская область"],
                "platform_url": "https://etp.gpb.ru/#com/procedure/view/procedure/1276253",
                "procedure_type_name": "Запрос предложений",
                "section_category_name": "Закупки.Бизнес223",
                "stage": "accepting",
                "date_published": "2026-07-30T13:41:00.000+03:00",
                "end_registration": "2026-08-11T09:00:00.000+03:00",
            },
        },
        {
            "id": "44-id",
            "type": "procedure",
            "attributes": {
                "registry_number": "0334400002726000083",
                "title": "Поставка и монтаж кондиционера",
                "company_name": "АРВПиС",
                "amount": "274928.94",
                "lot_regions": ["Красноярский край"],
                "platform_url": "https://gos.etpgpb.ru/front/procedure/view/test-uuid",
                "procedure_type_name": "Запрос котировок",
                "section_category_name": "Закупки.Гос44",
                "stage": "accepting",
                "date_published": "2026-08-04T12:03:33.000+03:00",
                "end_registration": "2026-08-11T05:00:00.000+03:00",
            },
        },
        {
            "id": "portal-id",
            "type": "procedure",
            "attributes": {
                "registry_number": "995357",
                "title": "Изготовление и монтаж LED-экранов",
                "company_name": "МИРАНДА-МЕДИА",
                "lot_regions": ["Республика Крым"],
                "platform_url": "https://etp.gpb.ru/#nsi/priceorder/directCustomer/orderId/995357",
                "procedure_type_name": "Ценовой запрос",
                "section_category_name": "Торговый портал",
                "stage": "accepting",
                "date_published": "2026-08-05T12:44:00.000+03:00",
                "end_registration": "2026-08-11T10:00:00.000+03:00",
            },
        },
        {
            "id": "commercial-id",
            "type": "procedure",
            "attributes": {
                "registry_number": "ГП643032",
                "title": "Коммерческая закупка",
                "platform_url": "https://etp.gpb.ru/#com/procedure/view/procedure/1",
                "section_category_name": "Закупки.Бизнес",
            },
        },
    ],
    "meta": {"total_pages": 1, "total_count": 4},
}


def test_build_api_url_searches_open_accepting_procedures() -> None:
    decoded = unquote_plus(build_api_url("монтаж крым", page=2, page_size=50))

    assert decoded.startswith("https://etpgpb.ru/api/v2/procedures/?")
    assert "page=2" in decoded
    assert "per=50" in decoded
    assert "search=монтаж крым" in decoded
    assert "procedure[stage][0]=accepting" in decoded


def test_parse_api_payload_keeps_223_44_and_trading_portal_only() -> None:
    tenders = parse_api_payload(SAMPLE_API, source_url="https://etpgpb.ru/api/v2/procedures/")

    assert len(tenders) == 3
    assert [item.tender_number for item in tenders] == [
        "32616251574",
        "0334400002726000083",
        "995357",
    ]
    assert [item.status for item in tenders] == [
        "Подача заявок · 223-ФЗ",
        "Подача заявок · 44-ФЗ",
        "Подача заявок · Торговый портал",
    ]
    assert tenders[0].price == 2_396_777.53
    assert tenders[0].deadline == datetime(2026, 8, 11, 9, 0)
    assert tenders[1].published_at == datetime(2026, 8, 4, 12, 3, 33)
    assert tenders[2].region == "Республика Крым"


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


class ApiResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class ApiSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> ApiResponse:
        self.requested_urls.append(url)
        return ApiResponse(SAMPLE_API)


def test_fetch_keywords_uses_configured_queries_and_dedupes() -> None:
    session = ApiSession()
    source = EtpGpbApiSource(session=session, queries=["мфу крым", "мфу севастополь"])

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 3
    assert len(session.requested_urls) == 2
    assert "search=" in session.requested_urls[0]


class TimeoutSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> ApiResponse:
        self.requested_urls.append(url)
        raise requests.Timeout("timed out")


def test_fetch_keywords_stops_after_configured_errors(monkeypatch) -> None:
    monkeypatch.setattr("tender_parser.http.sleep", lambda _: None)
    session = TimeoutSession()
    source = EtpGpbApiSource(
        session=session,
        queries=["мфу крым", "принтер крым", "кондиционер крым"],
        max_errors=1,
    )

    try:
        source.fetch_keywords(["ignored"])
    except SourceFetchError:
        pass

    # 1 запрос до лимита ошибок, с одним retry на timeout.
    assert len(session.requested_urls) == 2
