from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime

from tender_parser.models import TenderRecord
from tender_parser.regions import region_bucket
from tender_parser.text import normalize_text


SOURCE_PRIORITY = {
    "eis-regional-xml": 110,
    "eis-zakupki": 100,
    "tektorg": 97,
    "roseltorg": 95,
    "zakazrf": 95,
    "sberbank-ast": 95,
    "eat-berezka": 90,
    "tender-pro": 80,
    "etp-gpb": 70,
    "b2b-center": 65,
    "torgi82": 60,
    "rts-market": 50,
    "rostender": 10,
}

OFFICIAL_FIELDS = (
    "official_number",
    "official_url",
    "official_source",
    "platform_number",
    "platform_url",
    "procurement_law",
    "resolution_method",
    "resolution_confidence",
)


@dataclass(frozen=True)
class DeduplicationResult:
    tenders: list[TenderRecord]
    collapsed_count: int


@dataclass(frozen=True)
class _TenderCluster:
    """A merged record together with identities that must not be forgotten.

    ``TenderRecord`` deliberately keeps the preferred source-local number and
    URL.  The member list prevents a later pass from accidentally collapsing a
    second, different tender from that same source into the preferred record.
    """

    tender: TenderRecord
    members: tuple[TenderRecord, ...]


def deduplicate_tenders(tenders: list[TenderRecord]) -> DeduplicationResult:
    clusters = [_TenderCluster(tender=tender, members=(tender,)) for tender in tenders]

    # An official procurement number is a stronger identity than title, price
    # or deadline.  Only join different sources here: repeated cards within one
    # source still need the conservative source-local identity checks below.
    clusters = _merge_by_official_number(clusters)
    clusters = _merge_by_exact_card(clusters)

    unique = [cluster.tender for cluster in clusters]
    unique.sort(key=_sort_key)
    return DeduplicationResult(tenders=unique, collapsed_count=len(tenders) - len(unique))


def _merge_by_official_number(clusters: list[_TenderCluster]) -> list[_TenderCluster]:
    active: dict[int, _TenderCluster] = {}
    official_index: dict[str, set[int]] = {}
    source_number_index: dict[str, set[int]] = {}
    next_cluster_id = 0
    for cluster in clusters:
        current = cluster
        while True:
            candidate_ids = _official_candidate_ids(current, official_index, source_number_index)
            matching_id = next(
                (
                    candidate_id
                    for candidate_id in sorted(candidate_ids)
                    if candidate_id in active
                    and _official_identity_matches(active[candidate_id], current)
                    and _official_clusters_compatible(active[candidate_id], current)
                ),
                None,
            )
            if matching_id is None:
                break
            current = _merge_clusters(active.pop(matching_id), current)

        cluster_id = next_cluster_id
        next_cluster_id += 1
        active[cluster_id] = current
        for key in _cluster_official_numbers(current):
            official_index.setdefault(key, set()).add(cluster_id)
        for key in _cluster_source_numbers(current):
            source_number_index.setdefault(key, set()).add(cluster_id)

    return list(active.values())


def _merge_by_exact_card(clusters: list[_TenderCluster]) -> list[_TenderCluster]:
    grouped: dict[tuple[str, str], list[_TenderCluster]] = {}
    unique: list[_TenderCluster] = []

    for cluster in clusters:
        key = _duplicate_key(cluster.tender)
        if key is None:
            unique.append(cluster)
            continue
        candidates = grouped.setdefault(key, [])
        for index, current in enumerate(candidates):
            if _exact_card_clusters_compatible(current, cluster):
                candidates[index] = _merge_clusters(current, cluster)
                break
        else:
            candidates.append(cluster)

    for candidates in grouped.values():
        unique.extend(candidates)
    return unique


def _duplicate_key(tender: TenderRecord) -> tuple[str, str] | None:
    if tender.price is None or tender.deadline is None:
        return None
    title = re.sub(r"[^\w]+", "", normalize_text(tender.title))
    if not title:
        return None
    return title, tender.deadline.date().isoformat()


def _official_number_key(tender: TenderRecord) -> str | None:
    value = getattr(tender, "official_number", None)
    return _number_key(value)


def _number_key(value: object | None) -> str | None:
    if not value:
        return None
    key = re.sub(r"\s+", "", str(value)).casefold()
    return key or None


def _cluster_official_numbers(cluster: _TenderCluster) -> set[str]:
    return {
        key
        for tender in cluster.members
        if (key := _official_number_key(tender)) is not None
    }


def _cluster_source_numbers(cluster: _TenderCluster) -> set[str]:
    return {
        key
        for tender in cluster.members
        if (key := _number_key(tender.tender_number)) is not None
    }


def _official_candidate_ids(
    cluster: _TenderCluster,
    official_index: dict[str, set[int]],
    source_number_index: dict[str, set[int]],
) -> set[int]:
    candidates: set[int] = set()
    for key in _cluster_official_numbers(cluster):
        candidates.update(official_index.get(key, ()))
        candidates.update(source_number_index.get(key, ()))
    for key in _cluster_source_numbers(cluster):
        candidates.update(official_index.get(key, ()))
    return candidates


def _official_identity_matches(left: _TenderCluster, right: _TenderCluster) -> bool:
    left_official = _cluster_official_numbers(left)
    right_official = _cluster_official_numbers(right)
    left_source = _cluster_source_numbers(left)
    right_source = _cluster_source_numbers(right)
    return bool(
        left_official & (right_official | right_source)
        or right_official & left_source
    )


def _official_clusters_compatible(left: _TenderCluster, right: _TenderCluster) -> bool:
    # The official-number pass is intentionally cross-source only.  Requiring
    # disjoint source sets also prevents an indirect A+B+C merge from hiding two
    # different source-local numbers from source A.
    return (
        _cluster_sources(left).isdisjoint(_cluster_sources(right))
        and not _has_conflicting_official_numbers(left, right)
    )


def _exact_card_clusters_compatible(left: _TenderCluster, right: _TenderCluster) -> bool:
    if not _cluster_prices_within_one_ruble(left, right):
        return False
    if not _cluster_regions_compatible(left, right):
        return False
    if _has_conflicting_source_numbers(left, right):
        return False
    if _has_conflicting_official_numbers(left, right):
        return False
    return True


def _cluster_prices_within_one_ruble(left: _TenderCluster, right: _TenderCluster) -> bool:
    prices = [
        tender.price
        for tender in (*left.members, *right.members)
        if tender.price is not None
    ]
    if len(prices) < 2:
        return False
    return max(prices) - min(prices) < 1.0


def _has_conflicting_source_numbers(left: _TenderCluster, right: _TenderCluster) -> bool:
    numbers_by_source: dict[str, set[str]] = {}
    for tender in (*left.members, *right.members):
        if not tender.tender_number:
            continue
        source = normalize_text(tender.source)
        number = normalize_text(str(tender.tender_number))
        if number:
            numbers_by_source.setdefault(source, set()).add(number)
    return any(len(numbers) > 1 for numbers in numbers_by_source.values())


def _has_conflicting_official_numbers(left: _TenderCluster, right: _TenderCluster) -> bool:
    numbers = {
        key
        for tender in (*left.members, *right.members)
        if (key := _official_number_key(tender)) is not None
    }
    return len(numbers) > 1


def _cluster_sources(cluster: _TenderCluster) -> set[str]:
    return {normalize_text(tender.source) for tender in cluster.members}


def _cluster_regions_compatible(left: _TenderCluster, right: _TenderCluster) -> bool:
    left_buckets = {_region_bucket(tender) for tender in left.members} - {""}
    right_buckets = {_region_bucket(tender) for tender in right.members} - {""}
    return not left_buckets or not right_buckets or left_buckets == right_buckets


def _region_bucket(tender: TenderRecord) -> str:
    return region_bucket(" ".join([tender.region or "", tender.customer or "", tender.raw_text]))


def _merge_clusters(left: _TenderCluster, right: _TenderCluster) -> _TenderCluster:
    return _TenderCluster(
        tender=_merge_tenders(left.tender, right.tender),
        members=(*left.members, *right.members),
    )


def _merge_tenders(left: TenderRecord, right: TenderRecord) -> TenderRecord:
    preferred, alternate = sorted([left, right], key=lambda tender: _source_rank(tender.source), reverse=True)
    updates: dict[str, object] = {
        "customer": preferred.customer or alternate.customer,
        "region": preferred.region or alternate.region,
        "price": preferred.price if preferred.price is not None else alternate.price,
        "deadline": preferred.deadline or alternate.deadline,
        "status": preferred.status or alternate.status,
        "published_at": preferred.published_at or alternate.published_at,
        # Keep structured public contacts/documents contributed by an official
        # platform even when the EIS card wins source priority.
        "raw_text": _merge_raw_text(preferred.raw_text, alternate.raw_text),
    }
    # Keep the preferred source's official resolution, but fill every missing
    # field from the alternate card.  Source-local tender_number/url remain the
    # preferred record's values and therefore retain their provenance.
    for field_name in OFFICIAL_FIELDS:
        if hasattr(preferred, field_name):
            if field_name == "resolution_confidence":
                updates[field_name] = max(
                    float(getattr(preferred, field_name) or 0.0),
                    float(getattr(alternate, field_name) or 0.0),
                )
            else:
                updates[field_name] = getattr(preferred, field_name) or getattr(alternate, field_name)
    return replace(preferred, **updates)


def _source_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 40)


def _merge_raw_text(preferred: str, alternate: str) -> str:
    if not preferred:
        return alternate
    if not alternate or alternate in preferred:
        return preferred
    if preferred in alternate:
        return alternate
    return f"{preferred}\n{alternate}"


def _sort_key(tender: TenderRecord) -> tuple[datetime, str]:
    return tender.deadline or datetime.max, normalize_text(tender.title)
