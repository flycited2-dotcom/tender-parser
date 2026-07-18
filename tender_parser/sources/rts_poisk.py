from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tender_parser.models import TenderRecord
from tender_parser.regions import detect_region
from tender_parser.text import parse_price_rub


POISK_SOURCE_NAME = "rts-poisk"


def parse_poisk_page(html: str, source_url: str) -> list[TenderRecord]:
    """Разбирает выдачу поиска RTS по всем ЭТП (www.rts-tender.ru/poisk).

    Селекторы сверены с живой страницей 2026-07-03: карточки div.card-item,
    цена в itemprop=price, сроки в time[itemprop], организатор в блоке organization.
    """
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for card in soup.select("div.card-item"):
        title = _text(card.select_one(".card-item__title"))
        if not title:
            continue

        about_link = card.select_one(".card-item__about a[href]")
        number = _extract_number(_text(about_link))
        url = _card_url(card, about_link, source_url)
        # Без номера, без CardId и без собственной ссылки карточки неразличимы —
        # они бы схлопнулись в одну запись по unique_key.
        if not number and not _card_id(card) and url == source_url:
            continue

        customer = _customer(card)
        raw_text = _clean(card.get_text(" ", strip=True))
        tenders.append(
            TenderRecord(
                title=title,
                url=url,
                source=POISK_SOURCE_NAME,
                tender_number=number or _card_id(card),
                customer=customer,
                region=detect_region(" ".join([title, customer or "", raw_text])),
                price=_price(card),
                deadline=_time_value(card, "availabilityEnds"),
                status=_property_value(card, "статус") or "Актуально",
                published_at=_time_value(card, "availabilityStarts"),
                discovered_at=datetime.now(),
                raw_text=raw_text,
                detail_status="enriched",
                source_confidence=0.8,
            )
        )
    return tenders


def _text(element) -> str:
    if element is None:
        return ""
    return _clean(element.get_text(" ", strip=True))


def _clean(value: str) -> str:
    return " ".join(value.split())


def _extract_number(value: str) -> str | None:
    match = re.search(r"№\s*([\w/-]+)", value)
    return match.group(1) if match else None


def _card_id(card) -> str | None:
    node = card.select_one('input[name="Card.CardId"]')
    if node is not None and node.get("value"):
        return str(node["value"])
    return None


def _card_url(card, about_link, source_url: str) -> str:
    if about_link is not None and about_link.get("href"):
        return urljoin(source_url, str(about_link["href"]))
    details = card.select_one(".card-item__buttons a[href]")
    if details is not None and details.get("href"):
        return urljoin(source_url, str(details["href"]))
    return source_url


def _customer(card) -> str | None:
    link = card.select_one(".card-item__organization-main a")
    if link is None:
        return None
    value = _text(link)
    return re.sub(r"\s*\(все закупки\)\s*$", "", value).strip() or None


def _price(card) -> float | None:
    node = card.select_one('.card-item__properties [itemprop="price"]')
    if node is None:
        return None
    content = node.get("content")
    if content:
        try:
            return float(str(content))
        except ValueError:
            pass
    return parse_price_rub(node.get_text(" ", strip=True), require_currency=False)


def _property_value(card, needle: str) -> str | None:
    for cell in card.select(".card-item__properties-cell"):
        name = _text(cell.select_one(".card-item__properties-name")).lower()
        if needle in name:
            value = _text(cell.select_one(".card-item__properties-desc"))
            return value if value and value != "-" else None
    return None


def _time_value(card, itemprop: str) -> datetime | None:
    node = card.select_one(f'time[itemprop="{itemprop}"]')
    if node is None or not node.get("datetime"):
        return None
    raw = str(node["datetime"]).strip()[:19].strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None
