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
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import parse_price_rub


ROSELTORG_SEARCH_URL = "https://www.roseltorg.ru/procedures/search"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.4"
ACTIVE_STATUS_IDS = ("5", "0")
REGION_IDS = {
    "симферополь": ("91", "Республика Крым"),
    "республика крым": ("91", "Республика Крым"),
    "крым": ("91", "Республика Крым"),
    "севастополь": ("92", "Севастополь"),
    "запорожская область": ("90", "Запорожская область"),
    "херсонская область": ("95", "Херсонская область"),
}


def target_regions(regions: Iterable[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for region in regions:
        mapped = REGION_IDS.get(region.strip().casefold())
        if mapped and mapped[0] not in seen:
            seen.add(mapped[0])
            result.append(mapped)
    return result


def build_search_url(query: str, region_ids: Iterable[str]) -> str:
    params: list[tuple[str, str]] = [("query_field", query)]
    params.extend(("status[]", value) for value in ACTIVE_STATUS_IDS)
    params.extend(("region[]", value) for value in region_ids)
    return f"{ROSELTORG_SEARCH_URL}?{urlencode(params)}"


def parse_search_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for card in soup.select(".search-results__item"):
        title_link = card.select_one(".search-results__link--description")
        if title_link is None or not title_link.get("href"):
            continue
        title = _text(title_link)
        number = str(card.get("data-feature-favorite-lots-procedure-number") or "").strip()
        if not title or not number:
            continue

        status = _text(card.select_one(".search-results__status")) or "Актуально"
        section = _text(card.select_one(".search-results__section"))
        procedure_type = _text(card.select_one(".search-results__type"))
        region = _text(card.select_one(".search-results__region")) or None
        customer = _text(card.select_one(".search-results__customer p a")) or None
        raw_text = _text(card)
        price = parse_price_rub(_text(card.select_one(".search-results__sum p.desktop")))
        tenders.append(
            TenderRecord(
                title=title,
                url=urljoin(source_url, str(title_link["href"])),
                source="roseltorg",
                tender_number=number,
                customer=customer,
                region=region,
                price=price if price and price > 0 else None,
                deadline=_parse_datetime(_text(card.select_one(".search-results__time"))),
                status=" · ".join(value for value in [status, section] if value),
                discovered_at=datetime.now(),
                raw_text=" ".join(value for value in [raw_text, procedure_type] if value),
            )
        )
    return tenders


class RoseltorgSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        regions: list[str] | None = None,
        timeout_seconds: int = 15,
        max_errors: int = 3,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        # An empty product query plus an explicit regional filter discovers
        # non-thematic buyers for the CRM; focused queries remain for tenders.
        self.queries = queries or ["", *config.SEARCH_QUERY_TERMS]
        self.regions = target_regions(regions or config.SEARCH_REGION_TERMS)
        self.timeout_seconds = timeout_seconds
        self.max_errors = max_errors

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        region_ids = [region_id for region_id, _ in self.regions]
        for query in self.queries or list(keywords):
            url = build_search_url(query, region_ids)
            try:
                response = get_with_retry(self.session, url, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                errors.append(f"{query}: {exc}")
                if len(errors) >= self.max_errors:
                    break
                continue
            for tender in parse_search_page(response.text, source_url=url):
                if tender.unique_key in seen:
                    continue
                seen.add(tender.unique_key)
                collected.append(tender)

        if not collected and errors:
            raise SourceFetchError(f"Росэлторг недоступен: {'; '.join(errors)}")
        return collected


def _parse_datetime(value: str) -> datetime | None:
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+(?:в\s+)?(\d{2}:\d{2})", value)
    if not match:
        return None
    try:
        return datetime.strptime(f"{match.group(1)} {match.group(2)}", "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _text(element: object | None) -> str:
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())  # type: ignore[attr-defined]
