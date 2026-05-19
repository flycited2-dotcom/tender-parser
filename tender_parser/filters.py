from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from tender_parser.config import CATEGORY_KEYWORDS, MIN_PRICE_RUB, REGION_TERMS, STOP_TERMS
from tender_parser.models import TenderRecord
from tender_parser.text import normalize_text


STOP_TERM_VARIANTS = {
    "лекарственные препараты": ["лекарственных препаратов"],
}


def _first_matching_term(text: str, terms: list[str]) -> str | None:
    for term in terms:
        normalized = normalize_text(term)
        if normalized and normalized in text:
            return normalized
        for variant in STOP_TERM_VARIANTS.get(normalized, []):
            if normalize_text(variant) in text:
                return normalized
    return None


def _matching_category(text: str) -> tuple[str | None, list[str]]:
    for category, terms in CATEGORY_KEYWORDS.items():
        matches = [normalize_text(term) for term in terms if normalize_text(term) in text]
        if matches:
            return category, matches
    return None, []


def _exclude(tender: TenderRecord, reason: str) -> TenderRecord:
    return replace(
        tender,
        filter_status="excluded",
        category=None,
        include_reason="",
        exclude_reason=reason,
        matched_terms=[],
    )


def evaluate_tender(tender: TenderRecord, now: datetime | None = None) -> TenderRecord:
    current = now or datetime.now()
    searchable = normalize_text(" ".join([tender.title, tender.region or "", tender.customer or "", tender.raw_text]))

    stop_term = _first_matching_term(searchable, STOP_TERMS)
    if stop_term:
        return _exclude(tender, f"стоп-тема: {stop_term}")

    if tender.price is None or tender.price < MIN_PRICE_RUB:
        return _exclude(tender, f"сумма меньше {MIN_PRICE_RUB} или не указана")

    if tender.deadline is None or tender.deadline <= current:
        return _exclude(tender, "срок подачи истек или не указан")

    region = _first_matching_term(searchable, REGION_TERMS)
    if not region:
        return _exclude(tender, "регион не найден")

    category, terms = _matching_category(searchable)
    if not category:
        return _exclude(tender, "категория интереса не найдена")

    include_reason = (
        f"регион: {region}; категория: {category}; "
        f"ключевые слова: {', '.join(terms)}; сумма: {tender.price:.2f}; срок подачи активен"
    )
    return replace(
        tender,
        filter_status="matched",
        category=category,
        include_reason=include_reason,
        exclude_reason="",
        matched_terms=terms,
    )
