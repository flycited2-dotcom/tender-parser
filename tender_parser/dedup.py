from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime

from tender_parser.models import TenderRecord
from tender_parser.text import normalize_text


SOURCE_PRIORITY = {
    "eis-zakupki": 100,
    "eat-berezka": 90,
    "tender-pro": 80,
    "etp-gpb": 70,
    "b2b-center": 65,
    "torgi82": 60,
    "rts-market": 50,
    "rostender": 10,
}
REGION_BUCKETS = {
    "crimea": ["крым"],
    "sevastopol": ["севастопол"],
    "simferopol": ["симферопол"],
    "zaporizhzhia": ["запорож"],
    "kherson": ["херсон"],
}


@dataclass(frozen=True)
class DeduplicationResult:
    tenders: list[TenderRecord]
    collapsed_count: int


def deduplicate_tenders(tenders: list[TenderRecord]) -> DeduplicationResult:
    grouped: dict[tuple[str, float, str], list[TenderRecord]] = {}
    unique: list[TenderRecord] = []

    for tender in tenders:
        key = _duplicate_key(tender)
        if key is None:
            unique.append(tender)
            continue
        candidates = grouped.setdefault(key, [])
        for index, current in enumerate(candidates):
            if _regions_compatible(current, tender):
                candidates[index] = _merge_tenders(current, tender)
                break
        else:
            candidates.append(tender)

    for candidates in grouped.values():
        unique.extend(candidates)
    unique.sort(key=_sort_key)
    return DeduplicationResult(tenders=unique, collapsed_count=len(tenders) - len(unique))


def _duplicate_key(tender: TenderRecord) -> tuple[str, float, str] | None:
    if tender.price is None or tender.deadline is None:
        return None
    title = re.sub(r"[^\w]+", "", normalize_text(tender.title))
    if not title:
        return None
    return title, round(tender.price, 2), tender.deadline.date().isoformat()


def _regions_compatible(left: TenderRecord, right: TenderRecord) -> bool:
    left_bucket = _region_bucket(left)
    right_bucket = _region_bucket(right)
    return not left_bucket or not right_bucket or left_bucket == right_bucket


def _region_bucket(tender: TenderRecord) -> str:
    text = normalize_text(" ".join([tender.region or "", tender.customer or "", tender.raw_text]))
    for bucket, variants in REGION_BUCKETS.items():
        if any(variant in text for variant in variants):
            return bucket
    return ""


def _merge_tenders(left: TenderRecord, right: TenderRecord) -> TenderRecord:
    preferred, alternate = sorted([left, right], key=lambda tender: _source_rank(tender.source), reverse=True)
    return replace(
        preferred,
        customer=preferred.customer or alternate.customer,
        region=preferred.region or alternate.region,
        price=preferred.price if preferred.price is not None else alternate.price,
        deadline=preferred.deadline or alternate.deadline,
        status=preferred.status or alternate.status,
        published_at=preferred.published_at or alternate.published_at,
        raw_text=preferred.raw_text or alternate.raw_text,
    )


def _source_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 40)


def _sort_key(tender: TenderRecord) -> tuple[datetime, str]:
    return tender.deadline or datetime.max, normalize_text(tender.title)
