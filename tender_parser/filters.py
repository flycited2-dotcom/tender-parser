from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from tender_parser.config import CATEGORY_KEYWORDS, MIN_PRICE_RUB, STOP_TERMS
from tender_parser.models import MatchConfidence, ReviewPriority, TenderRecord
from tender_parser.regions import detect_region
from tender_parser.text import normalize_text, phrase_stems_match, word_term_matches


STOP_TERM_VARIANTS = {
    "лекарственные препараты": ["лекарственных препаратов"],
}


def _first_matching_term(text: str, terms: list[str]) -> str | None:
    for term in terms:
        normalized = normalize_text(term)
        if not normalized:
            continue
        if normalized in text:
            return normalized
        if " " in normalized and phrase_stems_match(text, normalized):
            return normalized
        for variant in STOP_TERM_VARIANTS.get(normalized, []):
            if normalize_text(variant) in text:
                return normalized
    return None


def matching_category(text: str) -> tuple[str | None, list[str]]:
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
        return word_term_matches(text, normalized)
    return normalized in text or phrase_stems_match(text, normalized)


def _exclude(tender: TenderRecord, reason: str) -> TenderRecord:
    return replace(
        tender,
        filter_status="excluded",
        category=None,
        include_reason="",
        exclude_reason=reason,
        match_confidence=None,
        review_priority="excluded",
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
    priority: ReviewPriority = "review",
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
        review_priority=priority,
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


def _subject_searchable(tender: TenderRecord) -> str:
    """Предмет закупки без имени заказчика — для стоп-тем и категорий."""
    raw_text = tender.raw_text
    if tender.customer:
        raw_text = raw_text.replace(tender.customer, " ")
    return normalize_text(" ".join([tender.title, raw_text]))


def evaluate_tender(tender: TenderRecord, now: datetime | None = None) -> TenderRecord:
    current = now or datetime.now()
    searchable = normalize_text(" ".join([tender.title, tender.region or "", tender.customer or "", tender.raw_text]))
    subject = _subject_searchable(tender)

    stop_term = _first_matching_term(subject, STOP_TERMS)
    if stop_term:
        return _exclude(tender, f"стоп-тема: {stop_term}")

    if tender.deadline is not None and tender.deadline <= current:
        return _exclude(tender, "срок подачи истек")

    category, terms = matching_category(subject)
    if not category:
        return _exclude(tender, "категория интереса не найдена")

    region = detect_region(searchable)
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
        priority: ReviewPriority = "review"
        if ("регион не найден" in missing and "сумма не указана" in missing) or (
            tender.source == "b2b-center" and ("регион не найден" in missing or "сумма не указана" in missing)
        ):
            priority = "wide"
        return _review(
            tender,
            category=category,
            terms=terms,
            reason="; ".join(missing),
            region=region,
            confidence=confidence,
            priority=priority,
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
        review_priority="hot",
        matched_terms=terms,
    )
