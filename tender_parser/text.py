from __future__ import annotations

import re
from datetime import datetime


TERM_SUFFIX_EXCEPTIONS = {
    "монитор": "инг",   # мониторинг
    "мышь": "як",       # мышьяк
    "щит": "овидн",     # щитовидная железа
    "фен": "(?:ол|ил|азеп|омен|тан|хел)",  # фенол, фенил-, феназепам, феномен, фентанил, фенхель
    "провод": "(?:ит|ил|им|ят)",  # проводится, проводились, проводимых — не провод
    "усо": "(?:п|верш)",  # усопших, усовершенствование — не стойка УСО
    "реализует": "ся",  # «закупка реализуется» — не продажа имущества
}

_STEM_VOWELS = "аеиоуыэюяь"


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.replace("\xa0", " ").replace(" ", " ").replace(" ", " ")
    cleaned = cleaned.replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def stem(word: str) -> str:
    return word[: max(4, min(7, len(word) - 2))]


MAX_STEM_GAP = 20


def phrase_stems_match(text: str, term: str) -> bool:
    """Матчит многословный термин по стемам слов по порядку — покрывает падежи.

    Каждый стем должен начинаться на границе слова (и не после дефиса), чтобы
    «административно-хозяйственный» не считался словом «хозяйственный». Разрыв
    между соседними стемами ограничен, чтобы «ремонт ... фасаде здания» не
    склеивался в стоп-фразу «ремонт здания».
    """
    stems = [stem(word) for word in term.split() if len(stem(word)) >= 4]
    if not stems:
        return False
    position = 0
    for index, part in enumerate(stems):
        match = re.compile(rf"(?<![\w])(?<!-){re.escape(part)}").search(text, position)
        if match is None:
            return False
        if index and match.start() - position > MAX_STEM_GAP:
            return False
        position = match.start() + len(part)
    return True


def _word_term_search(text: str, term: str, blocked: str | None) -> bool:
    # (?<!-) — часть составного слова после дефиса не считается словом («шприц-ручка» не «ручка»).
    suffix_guard = f"(?!{blocked})" if blocked else ""
    return re.search(rf"(?<![\w])(?<!-){re.escape(term)}{suffix_guard}[\w-]*(?![\w])", text) is not None


def word_term_matches(text: str, term: str) -> bool:
    if not term:
        return False
    blocked = TERM_SUFFIX_EXCEPTIONS.get(term)
    if _word_term_search(text, term, blocked):
        return True
    # Падежный fallback: «бумага» → стем «бумаг» покрывает «бумаги/бумагу».
    if term[-1] in _STEM_VOWELS and len(term) >= 5 and " " not in term:
        return _word_term_search(text, term[:-1], blocked)
    return False


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
        amount = float(cleaned)
    except ValueError:
        return None
    tail = text[match.end():]
    if re.match(r"\s*млн", tail):
        return amount * 1_000_000
    if re.match(r"\s*тыс", tail):
        return amount * 1_000
    return amount


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
