from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import sleep
from typing import Callable, Iterable, Mapping, Sequence

import requests
from bs4 import BeautifulSoup

from tender_parser.models import TenderRecord
from tender_parser.sources.eis import (
    EIS_SOURCE_NAME,
    build_search_url as build_eis_search_url,
    parse_search_page as parse_eis_search_page,
)


CACHE_VERSION = 1
DEFAULT_CACHE_PATH = Path("data/rostender_resolution_cache.json")
DEFAULT_LIMIT = 50
DEFAULT_DELAY_SECONDS = 0.75
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_RETRIES = 2
DEFAULT_NEGATIVE_TTL_HOURS = 24.0
MAX_RETRY_DELAY_SECONDS = 300.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.3"

# Rostender's public detail pages use this exact label in page metadata.  A
# colon is deliberately mandatory: numbers in phones, prices and Rostender's
# own eight-digit card IDs must never become official procurement numbers.
_OFFICIAL_NUMBER_RE = re.compile(
    # Current 223-ФЗ registry numbers use the 3xxxxxxxxxx shape.  Requiring
    # the leading 3 prevents an 11-digit Russian phone (7/8xxxxxxxxxx) from
    # being mistaken for a purchase even if malformed metadata labels it.
    r"(?<!\w)закупка\s*:\s*(?:№\s*)?(\d{19,20}|3\d{10})(?!\d)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class OfficialNumberExtraction:
    official_number: str | None
    candidates: tuple[str, ...] = ()
    conflict: bool = False


@dataclass(frozen=True)
class ResolutionResult:
    record: TenderRecord
    official_number: str | None = None
    official_url: str | None = None
    official_source: str | None = None
    platform_number: str | None = None
    platform_url: str | None = None
    procurement_law: str | None = None
    resolution_method: str | None = None
    resolution_confidence: float = 0.0
    checked_at: str | None = None
    error: str | None = None


def extract_official_number_details(html: str) -> OfficialNumberExtraction:
    """Extract an unambiguous official number from public page metadata only."""

    soup = BeautifulSoup(html, "html.parser")
    metadata: list[str] = []
    if soup.title is not None:
        metadata.append(soup.title.get_text(" ", strip=True))

    for meta in soup.find_all("meta"):
        property_name = str(meta.get("property") or "").strip().casefold()
        if property_name not in {"og:title", "og:description"}:
            continue
        content = str(meta.get("content") or "").strip()
        if content:
            metadata.append(content)

    candidates = tuple(
        sorted(
            {
                match.group(1)
                for value in metadata
                for match in _OFFICIAL_NUMBER_RE.finditer(value)
            }
        )
    )
    if len(candidates) != 1:
        return OfficialNumberExtraction(
            official_number=None,
            candidates=candidates,
            conflict=len(candidates) > 1,
        )
    return OfficialNumberExtraction(official_number=candidates[0], candidates=candidates)


def extract_official_number(html: str) -> str | None:
    """Return the official procurement number, or ``None`` when ambiguous."""

    return extract_official_number_details(html).official_number


def procurement_law_for_number(number: str) -> str | None:
    if len(number) == 11:
        return "223-ФЗ"
    if len(number) in {19, 20}:
        return "44-ФЗ"
    return None


def record_fingerprint(record: TenderRecord) -> str:
    payload = json.dumps(
        {
            "source": record.source,
            "tender_number": record.tender_number,
            "title": record.title,
            "url": record.url.split("#", 1)[0],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RostenderOfficialResolver:
    """Resolve shortlisted Rostender cards to public EIS/ETP identifiers.

    Only records explicitly passed to :meth:`resolve_shortlist` are opened.
    Any individual HTTP, parsing or cache error leaves that record untouched so
    this optional enrichment can never abort the main collection cycle.
    """

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        cache_path: str | Path | None = None,
        limit: int | None = None,
        delay_seconds: float | None = None,
        timeout_seconds: float | None = None,
        negative_ttl_hours: float | None = None,
        retries: int = DEFAULT_RETRIES,
        sleeper: Callable[[float], None] = sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.cache_path = Path(
            cache_path
            or os.getenv("ROSTENDER_RESOLUTION_CACHE_PATH", str(DEFAULT_CACHE_PATH))
        )
        self.limit = _env_int("ROSTENDER_RESOLUTION_LIMIT", DEFAULT_LIMIT) if limit is None else max(0, limit)
        self.delay_seconds = (
            _env_float("ROSTENDER_RESOLUTION_DELAY_SECONDS", DEFAULT_DELAY_SECONDS)
            if delay_seconds is None
            else max(0.0, delay_seconds)
        )
        self.timeout_seconds = (
            _env_float("ROSTENDER_RESOLUTION_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            if timeout_seconds is None
            else max(0.1, timeout_seconds)
        )
        self.negative_ttl_hours = (
            _env_float(
                "ROSTENDER_RESOLUTION_NEGATIVE_TTL_HOURS",
                DEFAULT_NEGATIVE_TTL_HOURS,
            )
            if negative_ttl_hours is None
            else max(0.0, negative_ttl_hours)
        )
        self.retries = max(0, retries)
        self._sleep = sleeper
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._request_count = 0
        self.last_results: list[ResolutionResult] = []

    def resolve_shortlist(
        self,
        shortlist: Iterable[TenderRecord],
        collected_official_records: Iterable[TenderRecord] = (),
    ) -> list[TenderRecord]:
        """Return enriched copies while preserving input order and provenance."""

        records = list(shortlist)
        official_index = _build_official_index(collected_official_records)
        cache = self._load_cache()
        entries = cache.setdefault("entries", {})
        changed = False
        processed = 0
        enriched: list[TenderRecord] = []
        results: list[ResolutionResult] = []

        for record in records:
            if record.source != "rostender" or processed >= self.limit:
                enriched.append(record)
                results.append(ResolutionResult(record=record))
                continue
            processed += 1
            fingerprint = record_fingerprint(record)
            cached = entries.get(fingerprint)
            try:
                resolved_record, result, entry = self._resolve_record(
                    record,
                    fingerprint=fingerprint,
                    cached=cached if isinstance(cached, Mapping) else None,
                    official_index=official_index,
                )
                if entry != cached:
                    entries[fingerprint] = entry
                    changed = True
            except Exception as exc:  # optional enrichment must be fail-open
                checked_at = self._checked_at()
                resolved_record = record
                result = ResolutionResult(
                    record=record,
                    checked_at=checked_at,
                    error=f"{type(exc).__name__}: {exc}",
                )
                entries[fingerprint] = {
                    "fingerprint": fingerprint,
                    "url": record.url,
                    "checked_at": checked_at,
                    "status": "error",
                    "error": result.error,
                }
                changed = True
            enriched.append(resolved_record)
            results.append(result)

        self.last_results = results
        if changed:
            self._save_cache(cache)
        return enriched

    def _resolve_record(
        self,
        record: TenderRecord,
        *,
        fingerprint: str,
        cached: Mapping[str, object] | None,
        official_index: Mapping[str, Sequence[TenderRecord]],
    ) -> tuple[TenderRecord, ResolutionResult, dict[str, object]]:
        entry = dict(cached or {})
        cached_status = str(entry.get("status") or "")
        official_number = str(entry.get("official_number") or "") or None
        checked_at = str(entry.get("checked_at") or "") or None

        # Successful extraction, a definite absence and a metadata conflict are
        # stable for the same fingerprint.  Transient HTTP errors are retried on
        # the next run instead of poisoning the cache.
        reusable_detail_cache = cached_status == "resolved" or (
            cached_status in {"unresolved", "conflict"}
            and self._negative_cache_is_fresh(checked_at)
        )
        if not reusable_detail_cache:
            response = self._get(record.url, retries=self.retries)
            extraction = extract_official_number_details(response.text)
            checked_at = self._checked_at()
            entry = {
                "fingerprint": fingerprint,
                "url": record.url,
                "checked_at": checked_at,
                "candidates": list(extraction.candidates),
            }
            if extraction.conflict:
                entry["status"] = "conflict"
                return (
                    record,
                    ResolutionResult(
                        record=record,
                        checked_at=checked_at,
                        resolution_method="rostender-meta-conflict",
                        error="conflicting official numbers in Rostender metadata",
                    ),
                    entry,
                )
            official_number = extraction.official_number
            if official_number is None:
                entry["status"] = "unresolved"
                return (
                    record,
                    ResolutionResult(
                        record=record,
                        checked_at=checked_at,
                        resolution_method="rostender-meta-no-number",
                    ),
                    entry,
                )
            entry["status"] = "resolved"
            entry["official_number"] = official_number

        if official_number is None:
            # Cached conflict/unresolved outcome.
            method = "rostender-meta-conflict" if cached_status == "conflict" else "rostender-meta-no-number"
            return (
                record,
                ResolutionResult(record=record, checked_at=checked_at, resolution_method=method),
                entry,
            )

        law = procurement_law_for_number(official_number)
        matches = list(official_index.get(official_number, ()))
        official_url: str | None = None
        official_source: str | None = None
        platform_number: str | None = None
        platform_url: str | None = None
        method = "rostender-meta"
        confidence = 0.95

        if matches:
            method = "rostender-meta+collected-exact"
            confidence = 1.0
            for match in matches:
                if _is_eis_record(match):
                    official_url = match.official_url or match.url
                    official_source = official_source or match.source
                    continue
                if platform_url is None:
                    platform_number = match.platform_number or match.tender_number
                    platform_url = match.platform_url or match.url

        if official_url is None and not matches:
            lookup_status = str(entry.get("eis_lookup_status") or "")
            cached_eis_url = str(entry.get("eis_url") or "") or None
            reusable_eis_cache = lookup_status == "exact" or (
                lookup_status == "not_found"
                and self._negative_cache_is_fresh(
                    str(entry.get("eis_checked_at") or "") or None
                )
            )
            if reusable_eis_cache and cached_eis_url:
                official_url = cached_eis_url
                method = (
                    "rostender-meta+eis-exact"
                    if lookup_status == "exact"
                    else "rostender-meta+eis-search-link"
                )
                confidence = 1.0 if lookup_status == "exact" else 0.95
            else:
                official_url, lookup_status, lookup_error = self._lookup_eis(official_number)
                entry["eis_lookup_status"] = lookup_status
                entry["eis_url"] = official_url
                entry["eis_checked_at"] = self._checked_at()
                if lookup_error:
                    entry["eis_error"] = lookup_error
                else:
                    entry.pop("eis_error", None)
                method = (
                    "rostender-meta+eis-exact"
                    if lookup_status == "exact"
                    else "rostender-meta+eis-search-link"
                )
                confidence = 1.0 if lookup_status == "exact" else 0.95
            official_source = EIS_SOURCE_NAME

        if official_url is None:
            # An exact ETP match is already useful, while the generic official
            # search link still gives the user a direct, login-free next step.
            official_url = build_eis_search_url(official_number)
            official_source = EIS_SOURCE_NAME

        enriched = replace(
            record,
            official_number=official_number,
            official_url=official_url,
            official_source=official_source,
            platform_number=platform_number,
            platform_url=platform_url,
            procurement_law=law,
            resolution_method=method,
            resolution_confidence=confidence,
        )
        result = ResolutionResult(
            record=enriched,
            official_number=official_number,
            official_url=official_url,
            official_source=official_source,
            platform_number=platform_number,
            platform_url=platform_url,
            procurement_law=law,
            resolution_method=method,
            resolution_confidence=confidence,
            checked_at=checked_at,
            error=str(entry.get("eis_error") or "") or None,
        )
        return enriched, result, entry

    def _lookup_eis(self, official_number: str) -> tuple[str, str, str | None]:
        search_url = build_eis_search_url(official_number)
        try:
            # One official search request: no pagination and no request retry.
            response = self._get(search_url, retries=0)
            candidates = parse_eis_search_page(response.text, source_url=search_url)
            exact = next(
                (record for record in candidates if record.tender_number == official_number),
                None,
            )
            if exact is not None:
                return exact.url, "exact", None
            return search_url, "not_found", None
        except Exception as exc:
            return search_url, "error", f"{type(exc).__name__}: {exc}"

    def _get(self, url: str, *, retries: int) -> object:
        attempt = 0
        while True:
            if self._request_count and self.delay_seconds:
                self._sleep(self.delay_seconds)
            self._request_count += 1
            response = self.session.get(url, timeout=self.timeout_seconds)
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code == 429 and attempt < retries:
                backoff = min(2.0**attempt, MAX_RETRY_DELAY_SECONDS)
                retry_after = _retry_after_seconds(
                    getattr(response, "headers", {}) or {},
                    now=self._now(),
                )
                self._sleep(min(max(backoff, retry_after), MAX_RETRY_DELAY_SECONDS))
                attempt += 1
                continue
            response.raise_for_status()
            return response

    def _load_cache(self) -> dict[str, object]:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": CACHE_VERSION, "entries": {}}
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return {"version": CACHE_VERSION, "entries": {}}
        if not isinstance(payload.get("entries"), dict):
            payload["entries"] = {}
        return payload

    def _save_cache(self, cache: Mapping[str, object]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(f"{self.cache_path.suffix}.tmp")
            temporary.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(self.cache_path)
        except OSError:
            # Cache is an optimisation, never a reason to fail the parser.
            return

    def _checked_at(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    def _negative_cache_is_fresh(self, checked_at: str | None) -> bool:
        if not checked_at or self.negative_ttl_hours <= 0:
            return False
        try:
            cached_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        current = self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age_hours = (current - cached_at).total_seconds() / 3600
        return 0 <= age_hours < self.negative_ttl_hours


def _build_official_index(records: Iterable[TenderRecord]) -> dict[str, list[TenderRecord]]:
    index: dict[str, list[TenderRecord]] = {}
    for record in records:
        if record.source == "rostender":
            continue
        for number in {record.official_number, record.tender_number}:
            if not number:
                continue
            value = number.strip()
            if procurement_law_for_number(value) is None:
                continue
            index.setdefault(value, []).append(record)
    return index


def _is_eis_record(record: TenderRecord) -> bool:
    return record.source == EIS_SOURCE_NAME or "zakupki.gov.ru" in record.url.casefold()


def _retry_after_seconds(headers: Mapping[str, object], *, now: datetime) -> float:
    raw_value = next(
        (str(value).strip() for key, value in headers.items() if key.casefold() == "retry-after"),
        "",
    )
    if not raw_value:
        return 0.0
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        try:
            target = parsedate_to_datetime(raw_value)
            current = now
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default
