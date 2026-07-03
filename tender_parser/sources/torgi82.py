from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Iterable

import requests
from bs4 import BeautifulSoup

from tender_parser.config import HTTP_TIMEOUT_SECONDS
from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import parse_price_rub


TORGI82_SEARCH_URL = "https://etp.torgi82.ru/searchServlet"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.2"


def build_search_url() -> str:
    return TORGI82_SEARCH_URL


def parse_search_payload(payload: dict[str, object]) -> list[TenderRecord]:
    data = payload.get("list")
    if not isinstance(data, list):
        return []

    tenders: list[TenderRecord] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        tender_number = _as_text(item.get("identifier"))
        title = _clean_text(_as_text(item.get("title")))
        if not tender_number or not title:
            continue
        customer = _extract_customer(item)
        url = _as_text(item.get("lotLink")) or _as_text(item.get("offerLink")) or TORGI82_SEARCH_URL
        raw_text = _raw_text(item, title, customer)
        tenders.append(
            TenderRecord(
                title=title,
                url=url,
                source="torgi82",
                tender_number=tender_number,
                customer=customer,
                price=_parse_price(_as_text(item.get("price"))),
                deadline=_parse_datetime(_as_text(item.get("gdEndDate"))),
                status=_extract_state(item),
                published_at=_parse_date(_as_text(item.get("placementDate"))),
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return tenders


class Torgi82Source:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,*/*"})

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        url = build_search_url()
        try:
            response = get_with_retry(self.session, url, timeout=HTTP_TIMEOUT_SECONDS)
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceFetchError(f"Торги82 недоступен: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceFetchError("Торги82 вернул неожиданный формат")
        return parse_search_payload(payload)


def _as_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_text(value: str) -> str:
    return " ".join(html.unescape(value).split())


def _extract_customer(item: dict[str, object]) -> str | None:
    customers = item.get("customer")
    if isinstance(customers, list) and customers:
        first = customers[0]
        if isinstance(first, dict):
            title = _as_text(first.get("title"))
            if title:
                return title
    organizer = item.get("organizer")
    if isinstance(organizer, dict):
        return _as_text(organizer.get("title")) or None
    return None


def _extract_state(item: dict[str, object]) -> str | None:
    state = item.get("state")
    if isinstance(state, dict):
        return _as_text(state.get("title")) or None
    return None


def _parse_price(value: str) -> float | None:
    if not value:
        return None
    text = BeautifulSoup(html.unescape(value), "html.parser").get_text(" ", strip=True)
    return parse_price_rub(text)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", html.unescape(value))
    match = re.search(r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", cleaned)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return None


def _raw_text(item: dict[str, object], title: str, customer: str | None) -> str:
    okdp2 = item.get("okdp2")
    okdp_text = " ".join(str(code) for code in okdp2) if isinstance(okdp2, list) else ""
    parts = [
        title,
        customer or "",
        _as_text(item.get("identifier")),
        _as_text(item.get("placementType")),
        _extract_state(item) or "",
        okdp_text,
    ]
    return " ".join(part for part in parts if part)
