import json
from datetime import datetime
from pathlib import Path

from tender_parser.models import TenderRecord
from tender_parser.sources.tender_pro import (
    TenderProSource,
    build_list_url,
    parse_list_payload,
)


SAMPLE_PAYLOAD = json.loads(Path("tests/fixtures/tender_pro_list_sample.json").read_text(encoding="utf-8"))


def test_build_list_url_uses_public_open_tenders_method() -> None:
    url = build_list_url(max_rows=200)

    assert url.startswith("https://www.tender.pro/api/_info.tenderlist_by_set.json?")
    assert "open_only=t" in url
    assert "set_type_id=2" in url
    assert "max_rows=200" in url


def test_parse_list_payload_extracts_tenders() -> None:
    tenders = parse_list_payload(SAMPLE_PAYLOAD, source_url="https://www.tender.pro/api/test")

    assert len(tenders) == 2
    assert tenders[0].source == "tender-pro"
    assert tenders[0].tender_number == "1199001"
    assert tenders[0].customer == 'ООО "Крымский заказчик"'
    assert tenders[0].region == "Россия, Республика Крым, Симферополь, ул. Центральная, 1"
    assert tenders[0].deadline == datetime(2026, 6, 4, 12, 0)
    assert tenders[0].published_at == datetime(2026, 5, 29, 0, 0)
    assert "_tender.info.json" in tenders[0].url
    assert tenders[1].deadline == datetime(2026, 6, 5, 23, 59)


class ListResponse:
    def __init__(self) -> None:
        self.text = json.dumps(SAMPLE_PAYLOAD)

    def json(self) -> dict[str, object]:
        return SAMPLE_PAYLOAD

    def raise_for_status(self) -> None:
        return None


class ListSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> ListResponse:
        self.requested_urls.append(url)
        return ListResponse()


def test_parse_list_payload_hides_api_key_from_record_urls() -> None:
    tenders = parse_list_payload(SAMPLE_PAYLOAD, source_url="https://www.tender.pro/api/test")

    assert all("_key=" not in tender.url for tender in tenders)


def test_fetch_keywords_accepts_boolean_success() -> None:
    payload = dict(SAMPLE_PAYLOAD)
    payload["success"] = True

    class BoolResponse(ListResponse):
        def json(self) -> dict[str, object]:
            return payload

    class BoolSession(ListSession):
        def get(self, url: str, timeout: int) -> ListResponse:
            self.requested_urls.append(url)
            return BoolResponse()

    tenders = TenderProSource(session=BoolSession()).fetch_keywords([])

    assert len(tenders) == 2


def test_fetch_keywords_requests_api_once() -> None:
    session = ListSession()
    source = TenderProSource(session=session, max_rows=50)

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert len(session.requested_urls) == 1
    assert "max_rows=50" in session.requested_urls[0]
