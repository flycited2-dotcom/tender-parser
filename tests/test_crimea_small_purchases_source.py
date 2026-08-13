from __future__ import annotations

from datetime import datetime

import requests

from tender_parser.sources.crimea_small_purchases import (
    CRIMEA_SMALL_PURCHASES_API_URL,
    CRIMEA_SMALL_PURCHASES_GRID_URL,
    CrimeaSmallPurchasesSource,
    build_notices_request,
    parse_notices_payload,
)


SAMPLE_ITEMS = [
    {
        "link": "7871847",
        "number": "ИМЗ-2026-006442",
        "name": "Поставка ботинок с высокими берцами",
        "uchr_sname": "ГУП РК КРЫМГАЗСЕТИ",
        "summa": "230766.59",
        "status": "Подача заявок",
        "collecting_startdate": "11.08.2026 11:52",
        "collecting_enddate": "14.08.2026 12:00",
        "pub_date": "11.08.2026",
        "reestr_type": "44",
        "marketplace": "3",
        "etp_url": "https://market.rts-tender.ru/search/sell/10565876",
        "okpd2_codes": "15.20.32.122",
    },
    {
        "link": "7874493",
        "number": "ИМЗ-2026-006456",
        "name": "Поставка ноутбуков",
        "uchr_sname": "ГБУЗ РК ЁЛОЧКА",
        "summa": "152646,66",
        "status": "Опубликовано",
        "collecting_enddate": "17.08.2026 17:00",
        "pub_date": "12.08.2026",
        "reestr_type": "44",
        "marketplace": "1",
        "etp_url": "",
        "okpd2_codes": "26.20.11.110",
    },
]


class FakeResponse:
    def __init__(
        self,
        *,
        text: str = "",
        payload: object | None = None,
        status_code: int = 200,
    ) -> None:
        self.text = text
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, get_response: FakeResponse, post_responses: list[FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.get_response = get_response
        self.post_responses = list(post_responses)
        self.get_calls: list[tuple[str, int]] = []
        self.post_calls: list[tuple[str, dict[str, object], dict[str, str], int]] = []

    def get(self, url: str, *, timeout: int) -> FakeResponse:
        self.get_calls.append((url, timeout))
        return self.get_response

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: int,
    ) -> FakeResponse:
        self.post_calls.append((url, json, headers, timeout))
        return self.post_responses.pop(0)


def test_build_notices_request_limits_page_size_and_filters_active_statuses() -> None:
    payload = build_notices_request(page=2, page_size=500)

    settings = payload["settings"]
    assert isinstance(settings, dict)
    assert settings["page"] == 2
    assert settings["rp"] == 30
    assert settings["sortField"] == "pub_date"
    assert settings["localFilter"] == [
        {
            "columnname": "status",
            "columntype": "ListQuoted",
            "operation": "In",
            "value": "Опубликовано,Подача заявок",
            "name": "status_select",
        }
    ]


def test_parse_notices_payload_normalizes_native_and_external_notices() -> None:
    tenders = parse_notices_payload({"totalRows": 2, "page": 0, "items": SAMPLE_ITEMS})

    assert len(tenders) == 2
    external, native = tenders
    assert external.source == "crimea-small-purchases"
    assert external.url == "https://market.rts-tender.ru/search/sell/10565876"
    assert external.price == 230_766.59
    assert external.deadline == datetime(2026, 8, 14, 12, 0)
    assert external.published_at == datetime(2026, 8, 11)
    assert external.region == "Республика Крым"
    assert "РТС-тендер" in (external.status or "")
    assert "15.20.32.122" in external.raw_text
    assert external.source_confidence == 0.95
    assert native.url.endswith("/GzwSP/Notice?link=7874493")
    assert native.price == 152_646.66
    assert "Портал малых закупок" in (native.status or "")


def test_parse_notices_payload_skips_rows_without_identity() -> None:
    assert parse_notices_payload({"items": [{"number": "1", "name": "Без ссылки"}]}) == []
    assert parse_notices_payload({"unexpected": []}) == []


def test_source_reads_public_token_posts_json_and_reports_health() -> None:
    session = FakeSession(
        FakeResponse(
            text='<form><input name="__RequestVerificationToken" value="token-123"></form>'
        ),
        [FakeResponse(payload={"totalRows": 2, "page": 0, "items": SAMPLE_ITEMS})],
    )
    source = CrimeaSmallPurchasesSource(session=session, timeout_seconds=7)

    result = source.fetch_with_report(["ноутбук"])

    assert len(result.tenders) == 2
    assert result.errors == []
    assert result.health[0].source == "crimea-small-purchases"
    assert result.health[0].status == "ok"
    assert result.health[0].found == 2
    assert session.get_calls == [(CRIMEA_SMALL_PURCHASES_GRID_URL, 7)]
    assert session.post_calls[0][0] == CRIMEA_SMALL_PURCHASES_API_URL
    assert session.post_calls[0][2]["RequestVerificationToken"] == "token-123"


def test_source_deduplicates_across_pages() -> None:
    session = FakeSession(
        FakeResponse(text='<input name="__RequestVerificationToken" value="token">'),
        [
            FakeResponse(payload={"totalRows": 3, "items": SAMPLE_ITEMS}),
            FakeResponse(payload={"totalRows": 3, "items": [SAMPLE_ITEMS[1]]}),
        ],
    )
    source = CrimeaSmallPurchasesSource(session=session, page_size=2, max_pages=2)

    result = source.fetch_with_report([])

    assert [item.tender_number for item in result.tenders] == [
        "ИМЗ-2026-006442",
        "ИМЗ-2026-006456",
    ]
    assert result.health[0].status == "partial"
    assert "прочитано 2 из 3" in result.health[0].detail
    assert len(session.post_calls) == 2


def test_source_reports_form_drift_instead_of_false_empty() -> None:
    session = FakeSession(FakeResponse(text="<html>Новая форма</html>"), [])
    source = CrimeaSmallPurchasesSource(session=session)

    result = source.fetch_with_report([])

    assert result.tenders == []
    assert result.health[0].status == "error"
    assert "защитный токен" in result.health[0].detail
    assert result.errors
