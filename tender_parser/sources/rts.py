from __future__ import annotations

import re
from dataclasses import dataclass
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
    RTS_MARKET_ENDPOINTS,
    RTS_MAX_PAGES_PER_KEYWORD,
)
from tender_parser.models import TenderRecord
from tender_parser.text import normalize_text, parse_deadline, parse_price_rub


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RTS-Tender-Parser/0.1"
BLOCKED_RESPONSE_MARKERS = [
    "превышен максимальный лимит скорости просмотра страниц",
    "регламент площадки не допускает использование ботов",
]


class SourceFetchError(RuntimeError):
    pass


class SourceBlockedError(SourceFetchError):
    pass


@dataclass(frozen=True)
class RtsMarketEndpoint:
    base_url: str
    source_name: str
    region_hint: str | None = None


def clean_url(url: str) -> str:
    return url.split("#", 1)[0]


def build_search_url(
    keyword: str,
    page_index: int = 0,
    base_url: str = RTS_MARKET_BASE_URL,
) -> str:
    params = {
        "searching": "1",
        "f_keyword": keyword,
        "price_start": str(MIN_PRICE_RUB),
    }
    if page_index:
        params["from"] = str(page_index * 20)
    return f"{base_url}?{urlencode(params)}"


def parse_market_page(
    html: str,
    source_url: str,
    source_name: str = "rts-rosatom",
    region_hint: str | None = None,
) -> list[TenderRecord]:
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
                source=source_name,
                tender_number=number_match.group(1) if number_match else None,
                customer=customer or None,
                price=parse_price_rub(price_text),
                deadline=parse_deadline(deadline_text),
                status="Актуально",
                published_at=parse_deadline(published_text),
                discovered_at=datetime.now(),
                raw_text=raw_text,
                region=region_hint or _extract_region(raw_text),
            )
        )
    return tenders


def is_blocked_response(url: str, html: str) -> bool:
    text = normalize_text(html)
    return "/captcha/" in url or any(marker in text for marker in BLOCKED_RESPONSE_MARKERS)


def _extract_region(text: str) -> str | None:
    normalized = normalize_text(text)
    for region in REGION_TERMS:
        if normalize_text(region) in normalized:
            return region
    return None


class RtsPublicSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        endpoints: list[RtsMarketEndpoint] | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.endpoints = endpoints or _default_endpoints()

    def fetch_keyword(
        self,
        keyword: str,
        max_pages: int = RTS_MAX_PAGES_PER_KEYWORD,
        endpoint: RtsMarketEndpoint | None = None,
    ) -> list[TenderRecord]:
        active_endpoint = endpoint or self.endpoints[0]
        tenders: list[TenderRecord] = []
        for page_index in range(max_pages):
            url = build_search_url(keyword, page_index, active_endpoint.base_url)
            response = self.session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            if is_blocked_response(response.url, response.text):
                raise SourceBlockedError(
                    "captcha: RTS-Tender ограничил скорость просмотра, отчет не обновлен"
                )
            page_tenders = parse_market_page(
                response.text,
                source_url=url,
                source_name=active_endpoint.source_name,
                region_hint=active_endpoint.region_hint,
            )
            if not page_tenders:
                break
            tenders.extend(page_tenders)
        return tenders

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        errors: list[str] = []
        completed_endpoints = 0
        seen: set[str] = set()
        for endpoint in self.endpoints:
            endpoint_failed = False
            for keyword in keywords:
                try:
                    fetched = self.fetch_keyword(keyword, endpoint=endpoint)
                except SourceFetchError as exc:
                    errors.append(f"{endpoint.source_name}: {exc}")
                    endpoint_failed = True
                    break
                except requests.RequestException as exc:
                    errors.append(f"{endpoint.source_name}: {exc}")
                    endpoint_failed = True
                    break

                for tender in fetched:
                    dedupe_key = tender.tender_number or tender.unique_key
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    collected.append(tender)

            if not endpoint_failed:
                completed_endpoints += 1

        if completed_endpoints == 0 and errors:
            raise SourceFetchError(f"все источники RTS недоступны: {'; '.join(errors)}")
        return collected


def _default_endpoints() -> list[RtsMarketEndpoint]:
    return [
        RtsMarketEndpoint(
            base_url=str(item["base_url"]),
            source_name=str(item["source_name"]),
            region_hint=item["region_hint"] if item["region_hint"] else None,
        )
        for item in RTS_MARKET_ENDPOINTS
    ]
