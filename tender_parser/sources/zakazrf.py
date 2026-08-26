from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from tender_parser import config
from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.sources.roseltorg import target_regions
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import parse_price_rub


ZAKAZRF_SEARCH_URL = "https://webppo.zakazrf.ru/NotificationEx"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.4"


def build_search_url(query: str, region_id: str, *, active_from: datetime) -> str:
    params = {
        "Filter": "1",
        "SelectedTabPage": "ALL",
        "FastFilter": query,
        "RegionRF": region_id,
        "SubmissionCloseDateTimeFrom": active_from.strftime("%d.%m.%Y"),
        "IsConstructionProcurement": "0",
        "IsGroup": "0",
        "QuantityUndefined": "0",
        "ContractBlocked": "0",
        "AsPublic": "0",
    }
    return f"{ZAKAZRF_SEARCH_URL}?{urlencode(params)}"


def parse_search_page(
    html: str,
    *,
    source_url: str,
    region_hint: str,
) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for row in soup.select("table.reporttable tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 12:
            continue
        number_link = cells[1].find("a", href=True)
        title = _text(cells[4])
        number = _text(number_link)
        if not title or not number or number == "(нет данных)":
            continue
        status = _text(cells[2])
        deadline = _parse_datetime(_text(cells[11]))
        tenders.append(
            TenderRecord(
                title=title,
                url=urljoin(source_url, str(number_link["href"])),
                source="zakazrf",
                tender_number=number,
                customer=_text(cells[7]) or _text(cells[6]) or None,
                region=region_hint,
                price=parse_price_rub(_text(cells[5]), require_currency=False),
                deadline=deadline,
                status=" · ".join(value for value in [status, _text(cells[0])] if value),
                published_at=_parse_date(_text(cells[9])),
                discovered_at=datetime.now(),
                raw_text=_text(row),
            )
        )
    return tenders


class ZakazRfSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        regions: list[str] | None = None,
        timeout_seconds: int = 15,
        max_errors: int = 3,
        now: datetime | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.queries = queries or ["", *config.SEARCH_QUERY_TERMS]
        self.regions = target_regions(regions or config.SEARCH_REGION_TERMS)
        self.timeout_seconds = timeout_seconds
        self.max_errors = max_errors
        self.now = now

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        active_from = self.now or datetime.now()
        for query in self.queries or list(keywords):
            for region_id, region_name in self.regions:
                url = build_search_url(query, region_id, active_from=active_from)
                try:
                    response = get_with_retry(self.session, url, timeout=self.timeout_seconds)
                except requests.RequestException as exc:
                    errors.append(f"{query}/{region_name}: {exc}")
                    if len(errors) >= self.max_errors:
                        break
                    continue
                for tender in parse_search_page(
                    response.text,
                    source_url=url,
                    region_hint=region_name,
                ):
                    if tender.unique_key in seen:
                        continue
                    seen.add(tender.unique_key)
                    collected.append(tender)
            if len(errors) >= self.max_errors:
                break

        if not collected and errors:
            raise SourceFetchError(f"Заказ РФ недоступен: {'; '.join(errors)}")
        return collected


def _parse_datetime(value: str) -> datetime | None:
    match = re.search(r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return None


def _text(element: object | None) -> str:
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())  # type: ignore[attr-defined]
