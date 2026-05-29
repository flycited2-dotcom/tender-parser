from __future__ import annotations

from datetime import datetime
from typing import Iterable
from urllib.parse import urlencode

import requests

from tender_parser.config import (
    HTTP_TIMEOUT_SECONDS,
    TENDER_PRO_API_KEY,
    TENDER_PRO_MAX_ROWS,
    TENDER_PRO_SET_ID,
)
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError


TENDER_PRO_LIST_URL = "https://www.tender.pro/api/_info.tenderlist_by_set.json"
TENDER_PRO_DETAIL_URL = "https://www.tender.pro/api/_tender.info.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.2"


def build_list_url(max_rows: int = TENDER_PRO_MAX_ROWS) -> str:
    params = {
        "_key": TENDER_PRO_API_KEY,
        "set_type_id": "2",
        "set_id": TENDER_PRO_SET_ID,
        "max_rows": str(max_rows),
        "open_only": "t",
    }
    return f"{TENDER_PRO_LIST_URL}?{urlencode(params)}"


def parse_list_payload(payload: dict[str, object], source_url: str) -> list[TenderRecord]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if not isinstance(data, list):
        return []

    tenders: list[TenderRecord] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        tender_id = item.get("id")
        title = _as_text(item.get("title"))
        if tender_id is None or not title:
            continue

        company_id = item.get("company_id")
        detail_url = _build_detail_url(tender_id, company_id)
        delivery_address = _as_text(item.get("delivery_address")) or None
        raw_text = " ".join(
            part
            for part in [
                title,
                _as_text(item.get("company_name")),
                delivery_address or "",
                _as_text(item.get("type_name")),
                source_url,
            ]
            if part
        )
        tenders.append(
            TenderRecord(
                title=title,
                url=detail_url,
                source="tender-pro",
                tender_number=str(tender_id),
                customer=_as_text(item.get("company_name")) or None,
                region=delivery_address,
                price=None,
                deadline=_parse_deadline(item),
                status=_as_text(item.get("type_name")) or "Актуально",
                published_at=_parse_date(_as_text(item.get("open_date"))),
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return tenders


class TenderProSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        max_rows: int = TENDER_PRO_MAX_ROWS,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.max_rows = max_rows

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        url = build_list_url(max_rows=self.max_rows)
        try:
            response = self.session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceFetchError(f"Tender.Pro API недоступен: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("success") != "true":
            raise SourceFetchError("Tender.Pro API вернул неуспешный ответ")
        return parse_list_payload(payload, source_url=url)


def _as_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _build_detail_url(tender_id: object, company_id: object) -> str:
    params = {
        "_key": TENDER_PRO_API_KEY,
        "id": str(tender_id),
    }
    if company_id is not None:
        params["company_id"] = str(company_id)
    return f"{TENDER_PRO_DETAIL_URL}?{urlencode(params)}"


def _parse_deadline(item: dict[str, object]) -> datetime | None:
    value = _as_text(item.get("date_time_sogl"))
    if value:
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    close_date = _parse_date(_as_text(item.get("close_date")))
    if close_date is None:
        return None
    return close_date.replace(hour=23, minute=59)


def _parse_datetime(value: str) -> datetime | None:
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return None
