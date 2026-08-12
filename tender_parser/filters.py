from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from tender_parser import config
from tender_parser.models import MatchConfidence, ReviewPriority, TenderRecord
from tender_parser.regions import (
    detect_delivery_region,
    detect_non_target_region,
    detect_region,
)
from tender_parser.text import normalize_text, phrase_stems_match, word_term_matches


STOP_TERM_VARIANTS = {
    "лекарственные препараты": ["лекарственных препаратов"],
}


def _first_matching_term(text: str, terms: list[str]) -> str | None:
    for term in terms:
        normalized = normalize_text(term)
        if not normalized:
            continue
        if " " in normalized:
            if normalized in text or phrase_stems_match(text, normalized):
                return normalized
        elif word_term_matches(text, normalized):
            return normalized
        for variant in STOP_TERM_VARIANTS.get(normalized, []):
            if normalize_text(variant) in text:
                return normalized
    return None


def matching_category(text: str) -> tuple[str | None, list[str]]:
    for category, terms in config.CATEGORY_KEYWORDS.items():
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
    """Предмет закупки без имени заказчика — для стоп-тем и категорий.

    Вырезаем заказчика по нормализованному тексту: иначе другой регистр или ё
    в написании имени ломает replace, и стоп-тема из имени убивает тендер.
    """
    subject = normalize_text(" ".join([tender.title, tender.raw_text]))
    customer = normalize_text(tender.customer)
    if customer:
        subject = subject.replace(customer, " ")
    return subject


def _resolve_target_region(tender: TenderRecord) -> tuple[str | None, str | None]:
    """Определяет целевой регион и, отдельно, причину строгого отсева.

    Поле из документов и явный контекст доставки важнее адреса заказчика. При
    этом структурированный нецелевой ``region`` нельзя перебить случайным словом
    «Крым» из выпадающего списка/шаблона страницы.
    """
    evidence_region = detect_region(tender.delivery_region_evidence)
    title_region = detect_region(tender.title)
    declared_region = detect_region(tender.region)
    delivery_region = detect_delivery_region(tender.raw_text)

    for strong_region in (evidence_region, title_region, delivery_region, declared_region):
        if strong_region:
            return strong_region, None

    if tender.region:
        return None, "регион не целевой"

    # Если структурированного региона нет, локальный заказчик или иной текст
    # карточки всё ещё являются полезным подтверждением целевого охвата.
    unstructured_region = detect_region(
        " ".join([tender.customer or "", tender.raw_text])
    )
    if unstructured_region:
        return unstructured_region, None

    non_target = detect_non_target_region(
        " ".join(
            [
                tender.title,
                tender.customer or "",
                tender.raw_text,
                tender.delivery_region_evidence,
            ]
        )
    )
    if non_target:
        return None, f"регион не целевой: {non_target}"

    if tender.source in config.STRICT_TARGET_REGION_SOURCES:
        return None, "целевой регион не подтвержден"
    return None, None


def evaluate_tender(tender: TenderRecord, now: datetime | None = None) -> TenderRecord:
    current = now or datetime.now()
    subject = _subject_searchable(tender)

    stop_term = _first_matching_term(subject, config.STOP_TERMS)
    if stop_term:
        return _exclude(tender, f"стоп-тема: {stop_term}")

    if tender.deadline is not None and tender.deadline <= current:
        return _exclude(tender, "срок подачи истек")

    category, terms = matching_category(subject)
    if not category:
        return _exclude(tender, "категория интереса не найдена")

    region, region_exclusion = _resolve_target_region(tender)
    if region_exclusion:
        return _exclude(tender, region_exclusion)

    if tender.price is not None and tender.price < config.MIN_PRICE_RUB:
        return _exclude(tender, f"сумма меньше {config.MIN_PRICE_RUB}")

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
