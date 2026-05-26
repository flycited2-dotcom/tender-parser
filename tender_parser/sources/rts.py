from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from tender_parser.config import (
    HTTP_TIMEOUT_SECONDS,
    MIN_PRICE_RUB,
    REGION_TERMS,
    RTS_MARKET_BASE_URL,
    RTS_MAX_PAGES_PER_KEYWORD,
)
from tender_parser.models import TenderRecord
from tender_parser.text import normalize_text, parse_deadline, parse_price_rub


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RTS-Tender-Parser/0.1"


def clean_url(url: str) -> str:
    return url.split("#", 1)[0]


def build_search_url(keyword: str, page_index: int = 0) -> str:
    params = {
        "searching": "1",
        "f_keyword": keyword,
        "price_start": str(MIN_PRICE_RUB),
    }
    if page_index:
        params["from"] = str(page_index * 20)
    return f"{RTS_MARKET_BASE_URL}?{urlencode(params)}"


def parse_market_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table.search-results tbody tr")
    tenders: list[TenderRecord] = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        link = cells[0].select_one("a.search-results-title")
        if link is None or not link.get("href"):
            continue

        display_title = " ".join(part.strip() for part in link.get_text(" ").split() if part.strip())
        number_match = re.search(r"№\s*(\d+)", display_title)
        customer = cells[1].get_text(" ", strip=True)
        price_text = cells[2].get_text(" ", strip=True)
        published_text = cells[3].get_text(" ", strip=True)
        deadline_text = cells[4].get_text(" ", strip=True)
        raw_text = row.get_text(" ", strip=True)

        tenders.append(
            TenderRecord(
                title=display_title,
                url=clean_url(urljoin(source_url, str(link["href"]))),
                source="rts-rosatom",
                tender_number=number_match.group(1) if number_match else None,
                customer=customer or None,
                price=parse_price_rub(price_text),
                deadline=parse_deadline(deadline_text),
                status="Актуально",
                published_at=parse_deadline(published_text),
                discovered_at=datetime.now(),
                raw_text=raw_text,
                region=_extract_region(raw_text),
            )
        )
    return tenders


def _extract_region(text: str) -> str | None:
    normalized = normalize_text(text)
    for region in REGION_TERMS:
        if normalize_text(region) in normalized:
            return region
    return None


class RtsPublicSource:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch_keyword(self, keyword: str, max_pages: int = RTS_MAX_PAGES_PER_KEYWORD) -> list[TenderRecord]:
        tenders: list[TenderRecord] = []
        for page_index in range(max_pages):
            url = build_search_url(keyword, page_index)
            response = self.session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            page_tenders = parse_market_page(response.text, source_url=url)
            if not page_tenders:
                break
            tenders.extend(page_tenders)
        return tenders

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        for keyword in keywords:
            for tender in self.fetch_keyword(keyword):
                if tender.unique_key in seen:
                    continue
                seen.add(tender.unique_key)
                collected.append(tender)
        return collected
