from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from tender_parser.config import EIS_MAX_ERRORS, EIS_SEARCH_QUERIES, EIS_TIMEOUT_SECONDS, REGION_TERMS
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import normalize_text, parse_price_rub


EIS_SEARCH_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html"
EIS_SOURCE_NAME = "eis-zakupki"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.2"
REGION_VARIANTS = {
    "Симферополь": ["симферополь", "симферополя"],
    "Севастополь": ["севастополь", "севастополя"],
    "Республика Крым": ["республика крым", "республике крым"],
    "Крым": ["крым"],
    "Запорожская область": ["запорожская область", "запорожской области"],
    "Херсонская область": ["херсонская область", "херсонской области"],
}


def build_search_url(query: str, page: int = 1) -> str:
    params = {
        "searchString": query,
        "morphology": "on",
        "pageNumber": str(page),
        "sortDirection": "false",
        "recordsPerPage": "_10",
        "showLotsInfoHidden": "false",
        "fz44": "on",
        "fz223": "on",
        "af": "on",
        "currencyIdGeneral": "-1",
    }
    return f"{EIS_SEARCH_URL}?{urlencode(params)}"


def parse_search_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for card in soup.select(".search-registry-entry-block"):
        title = _field_text(card, "Объект закупки")
        tender_number = _extract_number(card)
        url = _extract_url(card, source_url)
        if not title or not tender_number or not url:
            continue

        customer = _field_text(card, "Заказчик") or None
        raw_text = card.get_text(" ", strip=True)
        tenders.append(
            TenderRecord(
                title=title,
                url=url,
                source=EIS_SOURCE_NAME,
                tender_number=tender_number,
                customer=customer,
                region=_extract_region(raw_text),
                price=parse_price_rub(_text(card.select_one(".price-block__value"))),
                deadline=_parse_date(_data_value(card, "Окончание подачи заявок"), end_of_day=True),
                status=_text(card.select_one(".registry-entry__header-mid__title")) or "Актуально",
                published_at=_parse_date(_data_value(card, "Размещено"), end_of_day=False),
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return tenders


class EisZakupkiSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        max_pages_per_query: int = 1,
        timeout_seconds: int = EIS_TIMEOUT_SECONDS,
        max_errors: int = EIS_MAX_ERRORS,
    ) -> None:
        self.session = session or requests.Session()
        if session is None:
            self.session.trust_env = False
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.queries = queries or EIS_SEARCH_QUERIES
        self.max_pages_per_query = max_pages_per_query
        self.timeout_seconds = timeout_seconds
        self.max_errors = max_errors

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        queries = self.queries or list(keywords)
        for query in queries:
            for page in range(1, self.max_pages_per_query + 1):
                url = build_search_url(query, page=page)
                try:
                    response = self.session.get(url, timeout=self.timeout_seconds)
                    response.raise_for_status()
                except requests.RequestException as exc:
                    errors.append(f"{query}: {exc}")
                    break

                for tender in parse_search_page(response.text, source_url=url):
                    dedupe_key = tender.tender_number or tender.unique_key
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    collected.append(tender)
            if len(errors) >= self.max_errors:
                break

        if not collected and errors:
            raise SourceFetchError(f"ЕИС zakupki.gov.ru недоступен: {'; '.join(errors)}")
        return collected


def _field_text(card: object, label: str) -> str:
    for block in card.select(".registry-entry__body-block"):  # type: ignore[attr-defined]
        title = _text(block.select_one(".registry-entry__body-title"))
        if normalize_text(title) != normalize_text(label):
            continue
        value = block.select_one(".registry-entry__body-value") or block.select_one(".registry-entry__body-href")
        return _text(value)
    return ""


def _data_value(card: object, label: str) -> str:
    titles = list(card.select(".data-block__title"))  # type: ignore[attr-defined]
    for title in titles:
        if normalize_text(_text(title)) != normalize_text(label):
            continue
        sibling = title.find_next_sibling()
        while sibling is not None:
            classes = sibling.get("class", [])
            if "data-block__value" in classes:
                return _text(sibling)
            sibling = sibling.find_next_sibling()
    return ""


def _extract_number(card: object) -> str | None:
    value = _text(card.select_one(".registry-entry__header-mid__number"))  # type: ignore[attr-defined]
    match = re.search(r"№\s*([A-Za-zА-Яа-я0-9/-]+)", value)
    return match.group(1) if match else None


def _extract_url(card: object, source_url: str) -> str | None:
    link = card.select_one(".registry-entry__header-mid__number a")  # type: ignore[attr-defined]
    if link is None or not link.get("href"):
        return None
    return urljoin(source_url, str(link["href"]))


def _extract_region(raw_text: str) -> str | None:
    normalized = normalize_text(raw_text)
    for region, variants in REGION_VARIANTS.items():
        if any(variant in normalized for variant in variants):
            return region
    for region in REGION_TERMS:
        if normalize_text(region) in normalized:
            return region
    return None


def _parse_date(value: str, end_of_day: bool) -> datetime | None:
    if not value:
        return None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            if end_of_day and fmt == "%d.%m.%Y":
                return parsed.replace(hour=23, minute=59)
            return parsed
        except ValueError:
            continue
    return None


def _text(element: object | None) -> str:
    if element is None:
        return ""
    return element.get_text(" ", strip=True)  # type: ignore[attr-defined]
