from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from tender_parser.config import B2B_SEARCH_QUERIES, HTTP_TIMEOUT_SECONDS
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError


B2B_MARKET_URL = "https://www.b2b-center.ru/market/"
B2B_SOURCE_NAME = "b2b-center"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.3"


def build_search_url(query: str) -> str:
    params = {"f_keyword": query, "searching": "1"}
    return f"{B2B_MARKET_URL}?{urlencode(params)}"


def parse_market_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for row in soup.select("table.search-results tbody tr"):
        cells = row.find_all("td", recursive=False)
        title_link = row.select_one("a.search-results-title")
        if len(cells) < 4 or title_link is None or not title_link.get("href"):
            continue

        number_match = re.search(r"№\s*(\d+)", _text(title_link))
        title = _text(title_link.select_one(".search-results-title-desc")) or _text(title_link)
        if not number_match or not title:
            continue

        raw_text = " ".join(_text(cell) for cell in cells if _text(cell))
        tenders.append(
            TenderRecord(
                title=title,
                url=urljoin(source_url, str(title_link["href"])),
                source=B2B_SOURCE_NAME,
                tender_number=number_match.group(1),
                customer=_text(cells[1]) or None,
                deadline=_parse_datetime(_text(cells[3])),
                published_at=_parse_datetime(_text(cells[2])),
                status="Актуально",
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return tenders


class B2BCenterSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        max_errors: int = 3,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.queries = queries or B2B_SEARCH_QUERIES
        self.max_errors = max_errors

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        for query in self.queries or list(keywords):
            url = build_search_url(query)
            try:
                response = self.session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
                response.raise_for_status()
            except requests.RequestException as exc:
                errors.append(f"{query}: {exc}")
                if len(errors) >= self.max_errors:
                    break
                continue

            for tender in parse_market_page(response.text, source_url=url):
                dedupe_key = tender.tender_number or tender.unique_key
                if dedupe_key not in seen:
                    seen.add(dedupe_key)
                    collected.append(tender)

        if not collected and errors:
            raise SourceFetchError(f"B2B-Center недоступен: {'; '.join(errors)}")
        return collected


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _text(element: object | None) -> str:
    if element is None:
        return ""
    return element.get_text(" ", strip=True)  # type: ignore[attr-defined]
