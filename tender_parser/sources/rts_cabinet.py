from __future__ import annotations

import re
from datetime import datetime
from time import monotonic
from typing import Literal
from typing import Protocol
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import normalize_text, parse_deadline, parse_price_rub


CabinetState = Literal["results", "login", "blocked", "unknown"]


class CabinetBrowserClient(Protocol):
    def read_current_page(self) -> tuple[str, str]:
        ...


class RtsCabinetBrowserSource:
    def __init__(self, client: CabinetBrowserClient | None = None) -> None:
        self.client = client

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        result = self.fetch_with_report(keywords)
        if result.errors:
            raise SourceFetchError("; ".join(result.errors))
        return result.tenders

    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        started_at = monotonic()
        try:
            url, html = self._client().read_current_page()
        except Exception as exc:
            detail = f"chrome unavailable: {exc}"
            return _result("skipped", started_at, detail=detail, errors=[detail])

        state = detect_cabinet_state(html, url)
        if state == "login":
            detail = "login required: open RTS cabinet Chrome profile and sign in manually"
            return _result("blocked", started_at, detail=detail, errors=[detail])
        if state == "blocked":
            detail = "blocked: RTS shows captcha or anti-bot page"
            return _result("blocked", started_at, detail=detail, errors=[detail])
        if state == "unknown":
            detail = "unknown RTS cabinet page: open search results before running collector"
            return _result("error", started_at, detail=detail, errors=[detail])

        tenders = parse_cabinet_page(html, url)
        return _result("ok" if tenders else "empty", started_at, tenders=tenders)

    def _client(self) -> CabinetBrowserClient:
        if self.client is None:
            from tender_parser.browser.rts_cabinet import RtsCabinetBrowserClient

            self.client = RtsCabinetBrowserClient()
        return self.client


def detect_cabinet_state(html: str, url: str) -> CabinetState:
    normalized = normalize_text(f"{url} {html}")
    lower_html = html.lower()
    if "captcha" in normalized or "проверка безопасности" in normalized or "/captcha" in url.lower():
        return "blocked"
    if "вход в личный кабинет" in normalized or 'type="password"' in lower_html or "/login" in url.lower():
        return "login"
    if "номер процедуры" in normalized and ("наименование" in normalized or "нмцк" in normalized):
        return "results"
    return "unknown"


def parse_cabinet_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for table in soup.find_all("table"):
        headers = [_clean(cell.get_text(" ", strip=True)).lower() for cell in table.find_all("th")]
        if not _looks_like_results_table(headers):
            continue
        for row in table.select("tbody tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
            link = row.find("a", href=True)
            link_text = link.get_text(" ", strip=True) if link else ""
            title = _title_from_row(values, link_text)
            if not title:
                continue
            price_text = _value(headers, values, "нмцк") or _value(headers, values, "цена") or ""
            deadline_text = _value(headers, values, "окончание подачи") or ""
            published_text = _value(headers, values, "размещено") or ""
            tenders.append(
                TenderRecord(
                    title=title,
                    url=urljoin(source_url, str(link["href"])) if link else source_url,
                    source="rts-cabinet",
                    tender_number=_value(headers, values, "номер процедуры") or values[0],
                    customer=_value(headers, values, "заказчик"),
                    region=_value(headers, values, "регион"),
                    price=_parse_price(price_text),
                    deadline=parse_deadline(deadline_text),
                    published_at=parse_deadline(published_text),
                    status=_value(headers, values, "статус"),
                    discovered_at=datetime.now(),
                    raw_text=" ".join(value for value in values if value),
                    detail_status="enriched",
                    source_confidence=0.9,
                )
            )
    return tenders


def _looks_like_results_table(headers: list[str]) -> bool:
    joined = " ".join(headers)
    return "номер" in joined and ("наименование" in joined or "нмцк" in joined or "заказчик" in joined)


def _value(headers: list[str], values: list[str], needle: str) -> str | None:
    for index, header in enumerate(headers):
        if needle in header and index < len(values):
            return values[index] or None
    return None


def _title_from_row(values: list[str], link_text: str) -> str:
    if link_text:
        return _clean(link_text)
    for value in values:
        if len(value) > 12 and not value.upper().startswith("RTS-"):
            return value
    return ""


def _parse_price(value: str | None) -> float | None:
    parsed = parse_price_rub(value)
    if parsed is not None:
        return parsed
    text = normalize_text(value)
    if not text:
        return None
    match = re.search(r"\d[\d\s.,]*\d", text)
    if not match:
        return None
    cleaned = match.group(0).replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean(value: str) -> str:
    return " ".join(value.split())


def _result(
    status: Literal["ok", "empty", "skipped", "blocked", "error"],
    started_at: float,
    *,
    tenders: list[TenderRecord] | None = None,
    detail: str = "",
    errors: list[str] | None = None,
) -> SourceFetchResult:
    items = tenders or []
    return SourceFetchResult(
        tenders=items,
        health=[
            SourceHealth(
                source="rts-cabinet",
                status=status,
                found=len(items),
                elapsed_seconds=round(monotonic() - started_at, 3),
                detail=detail,
            )
        ],
        errors=errors or [],
    )
