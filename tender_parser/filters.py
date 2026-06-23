from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

from tender_parser.config import CATEGORY_KEYWORDS, MIN_PRICE_RUB, REGION_TERMS, STOP_TERMS
from tender_parser.models import MatchConfidence, TenderRecord
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
        matches = [normalize_text(term) for term in terms if _category_term_matches(text, term)]
        if matches:
            return category, matches
    return None, []


def _category_term_matches(text: str, term: str) -> bool:
    normalized = normalize_text(term)
    if not normalized:
        return False
    if " " not in normalized:
        if normalized == "монитор":
            return re.search(r"(?<![\w])монитор(?!инг)[\w-]*(?![\w])", text) is not None
        return re.search(rf"(?<![\w]){re.escape(normalized)}[\w-]*(?![\w])", text) is not None
    return normalized in text


def _exclude(tender: TenderRecord, reason: str) -> TenderRecord:
    return replace(
        tender,
        filter_status="excluded",
        category=None,
        include_reason="",
        exclude_reason=reason,
        match_confidence=None,
        matched_terms=[],
    )


def _review(
    tender: TenderRecord,
    *,
    category: str,
    terms: list[str],
    reason: str,
    region: str | None,
    confidence: MatchConfidence,
) -> TenderRecord:
    return replace(
        tender,
        filter_status="review",
        match_confidence=confidence,
        category=category,
        include_reason=_include_reason(
            category,
            terms,
            region,
            tender.price,
            deadline_is_active=tender.deadline is not None,
        ),
        exclude_reason=f"требуется проверка: {reason}",
        matched_terms=terms,
    )


def _include_reason(
    category: str,
    terms: list[str],
    region: str | None,
    price: float | None,
    deadline_is_active: bool,
) -> str:
    parts = []
    if region:
        parts.append(f"регион: {region}")
    parts.append(f"категория: {category}")
    parts.append(f"ключевые слова: {', '.join(terms)}")
    parts.append(f"сумма: {price:.2f}" if price is not None else "сумма: не указана")
    parts.append("срок подачи: активен" if deadline_is_active else "срок подачи: не указан")
    return "; ".join(parts)


def evaluate_tender(tender: TenderRecord, now: datetime | None = None) -> TenderRecord:
    current = now or datetime.now()
    searchable = normalize_text(" ".join([tender.title, tender.region or "", tender.customer or "", tender.raw_text]))

    stop_term = _first_matching_term(searchable, STOP_TERMS)
    if stop_term:
        return _exclude(tender, f"стоп-тема: {stop_term}")

    if tender.deadline is not None and tender.deadline <= current:
        return _exclude(tender, "срок подачи истек")

    category, terms = _matching_category(searchable)
    if not category:
        return _exclude(tender, "категория интереса не найдена")

    region = _first_matching_term(searchable, REGION_TERMS)
    if tender.region and not region:
        return _exclude(tender, "регион не целевой")

    if tender.price is not None and tender.price < MIN_PRICE_RUB:
        return _exclude(tender, f"сумма меньше {MIN_PRICE_RUB}")

    missing: list[str] = []
    if tender.deadline is None:
        missing.append("срок подачи не указан")
    if not region:
        missing.append("регион не найден")
    if tender.price is None:
        missing.append("сумма не указана")
    if missing:
        confidence: MatchConfidence = (
            "вероятное"
            if missing in (["срок подачи не указан"], ["сумма не указана"])
            else "ручная проверка"
        )
        return _review(
            tender,
            category=category,
            terms=terms,
            reason="; ".join(missing),
            region=region,
            confidence=confidence,
        )

    include_reason = _include_reason(
        category,
        terms,
        region,
        tender.price,
        deadline_is_active=True,
    )
    return replace(
        tender,
        filter_status="matched",
        match_confidence="точное",
        category=category,
        include_reason=include_reason,
        exclude_reason="",
        matched_terms=terms,
    )
