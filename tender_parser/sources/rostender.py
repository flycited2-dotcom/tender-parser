from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from tender_parser.config import HTTP_TIMEOUT_SECONDS, ROSTENDER_SEARCH_QUERIES
from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import normalize_text, parse_price_rub


ROSTENDER_BASE_URL = "https://rostender.info"
ROSTENDER_SEARCH_URL = f"{ROSTENDER_BASE_URL}/extsearch"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RTS-Tender-Parser/0.1"


def build_search_url(query: str, page: int = 1) -> str:
    params = {
        "keywords": query,
        "open": "1",
        "open_data": "1",
        "mode": "simple",
        "default_search": "0",
        "with_fo": "0",
    }
    if page > 1:
        params["page"] = str(page)
    return f"{ROSTENDER_SEARCH_URL}?{urlencode(params)}"


def parse_search_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for article in soup.select("article.tender-row"):
        title_link = article.select_one("a.tender-info__description")
        if title_link is None or not title_link.get("href"):
            continue

        title = title_link.get_text(" ", strip=True)
        tender_number = _extract_number(article)
        region = _extract_region(article)
        price = parse_price_rub(_text(article.select_one(".starting-price__price")))
        deadline = _parse_deadline(article)
        raw_text = article.get_text(" ", strip=True)

        tenders.append(
            TenderRecord(
                title=title,
                url=urljoin(source_url, str(title_link["href"])),
                source="rostender",
                tender_number=tender_number,
                region=region,
                price=price,
                deadline=deadline,
                status="Актуально",
                published_at=_parse_published_at(article),
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return tenders


class RostenderSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        max_pages_per_query: int = 1,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.queries = queries or ROSTENDER_SEARCH_QUERIES
        self.max_pages_per_query = max_pages_per_query

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        queries = self.queries or list(keywords)
        for query in queries:
            for page in range(1, self.max_pages_per_query + 1):
                url = build_search_url(query, page)
                try:
                    response = get_with_retry(self.session, url, timeout=HTTP_TIMEOUT_SECONDS)
                except requests.RequestException as exc:
                    errors.append(f"{query}: {exc}")
                    break

                for tender in parse_search_page(response.text, source_url=url):
                    dedupe_key = tender.tender_number or tender.unique_key
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    collected.append(tender)

        if not collected and errors:
            raise SourceFetchError(f"Rostender недоступен: {'; '.join(errors)}")
        return collected


def _text(element: object | None) -> str:
    if element is None:
        return ""
    return element.get_text(" ", strip=True)  # type: ignore[attr-defined]


def _extract_number(article: object) -> str | None:
    value = _text(article.select_one(".tender__number"))  # type: ignore[attr-defined]
    match = re.search(r"№\s*(\d+)", value)
    return match.group(1) if match else None


def _extract_region(article: object) -> str | None:
    regions = [
        region.get_text(" ", strip=True)
        for region in article.select("a.tender__region-link")  # type: ignore[attr-defined]
        if region.get_text(" ", strip=True)
    ]
    return "; ".join(regions) or None


def _parse_deadline(article: object) -> datetime | None:
    date_text = _text(article.select_one(".tender__countdown-text .black"))  # type: ignore[attr-defined]
    if not date_text:
        return None
    time_text = _text(article.select_one(".tender__countdown-container"))  # type: ignore[attr-defined]
    if time_text:
        value = f"{date_text} {time_text}"
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            parsed = datetime.strptime(date_text, fmt)
            return parsed.replace(hour=23, minute=59)
        except ValueError:
            continue
    return None


def _parse_published_at(article: object) -> datetime | None:
    value = normalize_text(_text(article.select_one(".tender__date-start")))  # type: ignore[attr-defined]
    match = re.search(r"(\d{2}\.\d{2}\.\d{2,4})", value)
    if not match:
        return None
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(match.group(1), fmt)
        except ValueError:
            continue
    return None
