from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tender_parser.models import TenderRecord
from tender_parser.regions import detect_region
from tender_parser.text import parse_price_rub


EAT_BROWSER_SOURCE_NAME = "eat-berezka"


def parse_eat_listing_page(html: str, source_url: str) -> list[TenderRecord]:
    """Parse short cards visible in the authenticated EAT supplier listing."""

    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for card in soup.select("app-purchase-card"):
        number_link = card.select_one('#tradeNumber a[href*="/announcement/"]')
        number = _text(number_link)
        title = _text(card.select_one("#subject"))
        if not number or not title or number_link is None:
            continue

        delivery_address = _text(card.select_one("#deliveryAddress"))
        raw_text = _text(card)
        tenders.append(
            TenderRecord(
                title=title,
                url=urljoin(source_url, str(number_link["href"])),
                source=EAT_BROWSER_SOURCE_NAME,
                tender_number=number,
                customer=_text(card.select_one("#organizerInfoNameLink")) or None,
                region=detect_region(delivery_address),
                price=parse_price_rub(
                    _text(card.select_one("#contractPrice")),
                    require_currency=False,
                ),
                deadline=None,
                status=_text(card.select_one("#purchaseStateDescription")) or "Актуально",
                discovered_at=datetime.now(),
                raw_text=raw_text,
                detail_status="listing_only",
                source_confidence=0.9,
            )
        )
    return tenders


def _text(element) -> str:
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())
