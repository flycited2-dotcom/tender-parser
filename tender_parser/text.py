from __future__ import annotations

import re
from datetime import datetime


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip().lower()


def parse_price_rub(value: str | None) -> float | None:
    text = normalize_text(value)
    if not text or "без указания цены" in text or "$" in text or "usd" in text:
        return None
    if "₽" not in text and "руб" not in text:
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


def parse_deadline(value: str | None) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
