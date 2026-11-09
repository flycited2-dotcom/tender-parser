"""Public DOM of market.rts-tender.ru, not the legacy /market table."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from tender_parser.models import TenderRecord
from tender_parser.regions import detect_region
from tender_parser.text import parse_deadline, parse_price_rub


MONTHS = ("январ", "феврал", "март", "апрел", "мая", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")


def parse_market_deadline(value: str, now: datetime) -> datetime | None:
    numeric = re.search(r"\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?", value)
    if numeric:
        return parse_deadline(numeric.group())
    match = re.search(r"(\d{1,2})\s+([а-я]+)(?:\s+(20\d{2}))?\s*,?\s*(\d{2}):(\d{2})", value.lower())
    if not match:
        return None
    month = next((i for i, stem in enumerate(MONTHS, 1) if match[2].startswith(stem)), None)
    if month is None:
        return None
    year = int(match[3]) if match[3] else now.year
    if not match[3] and now.month == 12 and month == 1:
        year += 1
    if not match[3] and now.month == 1 and month == 12:
        year -= 1
    try:
        return datetime(year, month, int(match[1]), int(match[4]), int(match[5]))
    except ValueError:
        return None


def parse_modern_market_page(html: str, source_url: str, *, now: datetime | None = None) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    current = now or datetime.now()
    result: dict[str, TenderRecord] = {}
    for title_node in soup.select('[rts-at="requestCardTileTitle"]'):
        card = title_node.find_parent("article") or title_node.find_parent("app-request-strip-short-card")
        if card is None:
            continue
        link = title_node.find_parent("a", href=True)
        if link is None:
            continue
        url = urljoin(source_url, str(link["href"]))
        parsed = urlparse(url)
        number = re.fullmatch(r"/search/sell/(\d+)(?:/request)?/?", parsed.path)
        if parsed.hostname != "market.rts-tender.ru" or number is None:
            continue

        def text(field: str) -> str:
            node = card.select_one(f'[rts-at="{field}"]')
            return " ".join(node.get_text(" ", strip=True).split()) if node else ""

        title = " ".join(title_node.get_text(" ", strip=True).split())
        if not title:
            continue
        region = text("requestCardRegionNameText")
        region_label = re.sub(r"^Регионы поставки:\s*", "", region, flags=re.IGNORECASE).strip()
        raw = " ".join(card.get_text(" ", strip=True).split())
        result[number[1]] = TenderRecord(
            title=title, url=url, source="rts-market-modern", tender_number=number[1],
            platform_number=number[1], platform_url=url, official_source="РТС-тендер",
            customer=text("requestCardCustomerName") or None,
            region=detect_region(region_label) or region_label or detect_region(raw), delivery_region_evidence=region,
            price=parse_price_rub(text("requestCardTradePrice"), require_currency=False),
            deadline=parse_market_deadline(text("requestCardAppEndDate"), current),
            status=text("requestCardAppStateIndicatorText") or None,
            procurement_law="223-ФЗ" if "223-ФЗ" in raw else ("44-ФЗ" if "44-ФЗ" in raw else None),
            discovered_at=current, raw_text=raw, source_confidence=0.9,
        )
    return list(result.values())
