from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from time import monotonic, sleep
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from tender_parser.config import (
    B2B_DETAIL_DELAY_SECONDS,
    B2B_MAX_DETAILS,
    B2B_SEARCH_QUERIES,
    HTTP_TIMEOUT_SECONDS,
)
from tender_parser.filters import matching_category
from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.regions import detect_region
from tender_parser.run_report import SourceFetchResult, SourceHealth, SourceStatus
from tender_parser.sources.rts import SourceBlockedError, SourceFetchError
from tender_parser.text import normalize_text, parse_deadline, parse_price_rub


B2B_MARKET_URL = "https://www.b2b-center.ru/market/"
B2B_SOURCE_NAME = "b2b-center"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.3"
B2B_BLOCKED_MARKERS = (
    "проверка безопасности",
    "подтвердите, что вы не робот",
    "введите код с картинки",
    "g-recaptcha",
    "hcaptcha",
)

# Метки в таблице карточки закупки B2B-Center (сверены с live-страницами 2026-07-03).
DETAIL_LABELS = {
    "price": "общая стоимость закупки",
    "deadline": "дата окончания подачи заявок",
    "published_at": "дата публикации",
    "customer": "заказчики",
    "organizer": "организатор",
    "delivery_address": "адрес места поставки",
}


@dataclass(frozen=True)
class B2BDetail:
    price: float | None = None
    deadline: datetime | None = None
    published_at: datetime | None = None
    customer: str | None = None
    delivery_address: str | None = None


def parse_detail_page(html: str) -> B2BDetail | None:
    """Разбирает карточку закупки; None, если ни одно поле не распознано."""
    soup = BeautifulSoup(html, "html.parser")
    values: dict[str, str] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 2:
            continue
        label = normalize_text(cells[0].get_text(" ", strip=True))
        value = cells[1].get_text(" ", strip=True)
        if not label or not value:
            continue
        for field, needle in DETAIL_LABELS.items():
            if label.startswith(needle) and field not in values:
                values[field] = value
    detail = B2BDetail(
        price=parse_price_rub(values.get("price")),
        deadline=parse_deadline(values.get("deadline")),
        published_at=parse_deadline(values.get("published_at")),
        customer=values.get("customer") or values.get("organizer"),
        delivery_address=values.get("delivery_address"),
    )
    if detail == B2BDetail():
        return None
    return detail


def is_detail_candidate(tender: TenderRecord) -> bool:
    if tender.price is not None and tender.region:
        return False
    category, _ = matching_category(normalize_text(" ".join([tender.title, tender.raw_text])))
    return category is not None


def _merge_detail(tender: TenderRecord, detail: B2BDetail) -> TenderRecord:
    # Канонический регион из адреса важнее; сырой адрес пишем только если региона не было.
    region = tender.region
    if detail.delivery_address:
        region = detect_region(detail.delivery_address) or tender.region or detail.delivery_address[:100]
    raw_text = " ".join(
        part for part in [tender.raw_text, detail.delivery_address or "", detail.customer or ""] if part
    )
    return replace(
        tender,
        price=tender.price if tender.price is not None else detail.price,
        deadline=tender.deadline or detail.deadline,
        published_at=tender.published_at or detail.published_at,
        customer=tender.customer or detail.customer,
        region=region,
        raw_text=raw_text,
        detail_status="enriched",
        source_confidence=max(tender.source_confidence, 0.8),
    )


def build_search_url(query: str) -> str:
    params = {"f_keyword": query, "searching": "1"}
    return f"{B2B_MARKET_URL}?{urlencode(params)}"


def is_blocked_page(url: str, html: str) -> bool:
    """Распознаёт anti-bot страницу, не путая её с тендером со словом captcha."""
    if "/captcha" in url.casefold():
        return True
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("table.search-results a.search-results-title") is not None:
        return False
    normalized = normalize_text(html)
    return any(marker in normalized for marker in B2B_BLOCKED_MARKERS)


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
                region=detect_region(raw_text),
                deadline=_parse_datetime(_text(cells[3])),
                published_at=_parse_datetime(_text(cells[2])),
                status="Актуально",
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return tenders


class B2BCenterSource:
    source_name = B2B_SOURCE_NAME

    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        max_errors: int = 3,
        max_details: int = B2B_MAX_DETAILS,
        detail_delay_seconds: float = B2B_DETAIL_DELAY_SECONDS,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.queries = queries or B2B_SEARCH_QUERIES
        self.max_errors = max_errors
        self.max_details = max_details
        self.detail_delay_seconds = detail_delay_seconds

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        result = self.fetch_with_report(keywords)
        if not result.tenders and result.errors:
            if result.health and result.health[0].status == "blocked":
                raise SourceBlockedError(result.errors[0])
            raise SourceFetchError(result.errors[0])
        return result.tenders

    def fetch_with_report(self, keywords: Iterable[str]) -> SourceFetchResult:
        started_at = monotonic()
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        blocked = False
        for query in self.queries or list(keywords):
            url = build_search_url(query)
            try:
                response = get_with_retry(self.session, url, timeout=HTTP_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                errors.append(f"{query}: {exc}")
                if len(errors) >= self.max_errors:
                    break
                continue

            response_url = str(getattr(response, "url", url))
            if is_blocked_page(response_url, response.text):
                errors.append(
                    f"{query}: B2B-Center перенаправил запрос на CAPTCHA/anti-bot страницу"
                )
                blocked = True
                break

            for tender in parse_market_page(response.text, source_url=url):
                dedupe_key = tender.tender_number or tender.unique_key
                if dedupe_key not in seen:
                    seen.add(dedupe_key)
                    collected.append(tender)

        detail_error = ""
        if collected:
            collected, detail_error = self._enrich_with_details(collected)
            if detail_error:
                errors.append(detail_error)
                blocked = True

        if errors and collected:
            status: SourceStatus = "partial"
        elif blocked:
            status = "blocked"
        elif errors:
            status = "error"
        elif collected:
            status = "ok"
        else:
            status = "empty"

        detail = "; ".join(errors[:3])
        prefixed_errors = [f"B2B-Center: {detail}"] if errors else []
        return SourceFetchResult(
            tenders=collected,
            health=[
                SourceHealth(
                    source=B2B_SOURCE_NAME,
                    status=status,
                    found=len(collected),
                    elapsed_seconds=round(monotonic() - started_at, 3),
                    detail=detail,
                )
            ],
            errors=prefixed_errors,
        )

    def _enrich_with_details(self, tenders: list[TenderRecord]) -> tuple[list[TenderRecord], str]:
        enriched: list[TenderRecord] = []
        budget = self.max_details
        for index, tender in enumerate(tenders):
            if budget <= 0 or not is_detail_candidate(tender):
                enriched.append(tender)
                continue
            budget -= 1
            try:
                detail = self._fetch_detail(tender.url)
            except SourceBlockedError:
                # Уже собранные карточки не теряем: выдача частичная, а не пустая.
                enriched.extend(tenders[index:])
                return enriched, "детальные карточки B2B-Center заблокированы CAPTCHA"
            enriched.append(_merge_detail(tender, detail) if detail else tender)
        return enriched, ""

    def _fetch_detail(self, url: str) -> B2BDetail | None:
        try:
            response = get_with_retry(self.session, url, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException:
            return None
        finally:
            if self.detail_delay_seconds:
                sleep(self.detail_delay_seconds)
        response_url = str(getattr(response, "url", url))
        if is_blocked_page(response_url, response.text):
            raise SourceBlockedError("B2B-Center detail CAPTCHA")
        return parse_detail_page(response.text)


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _text(element: object | None) -> str:
    if element is None:
        return ""
    return element.get_text(" ", strip=True)  # type: ignore[attr-defined]
