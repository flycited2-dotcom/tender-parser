from __future__ import annotations

import re
from datetime import datetime


TERM_SUFFIX_EXCEPTIONS = {
    "монитор": "инг",   # мониторинг
    "мышь": "як",       # мышьяк
    "щит": "овидн",     # щитовидная железа
    "фен": "(?:ол|ил|азеп|омен)",  # фенол, фенил-, феназепам, феномен
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.replace("\xa0", " ").replace(" ", " ").replace(" ", " ")
    cleaned = cleaned.replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def stem(word: str) -> str:
    return word[: max(4, min(7, len(word) - 2))]


def phrase_stems_match(text: str, term: str) -> bool:
    """Матчит многословный термин по стемам слов по порядку — покрывает падежи."""
    stems = [stem(word) for word in term.split() if len(stem(word)) >= 4]
    if not stems:
        return False
    position = 0
    for part in stems:
        found_at = text.find(part, position)
        if found_at < 0:
            return False
        position = found_at + len(part)
    return True


def word_term_matches(text: str, term: str) -> bool:
    # (?<!-) — часть составного слова после дефиса не считается словом («шприц-ручка» не «ручка»).
    if not term:
        return False
    blocked = TERM_SUFFIX_EXCEPTIONS.get(term)
    suffix_guard = f"(?!{blocked})" if blocked else ""
    return re.search(rf"(?<![\w])(?<!-){re.escape(term)}{suffix_guard}[\w-]*(?![\w])", text) is not None


def parse_price_rub(value: str | None, *, require_currency: bool = True) -> float | None:
    text = normalize_text(value)
    if not text or "без указания цены" in text or "$" in text or "usd" in text:
        return None
    if require_currency and "₽" not in text and "руб" not in text:
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
