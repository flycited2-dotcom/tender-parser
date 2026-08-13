from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Iterable, Protocol, cast

from tender_parser.models import (
    DetailStatus,
    FilterStatus,
    MatchConfidence,
    ReviewPriority,
    TenderRecord,
)
from tender_parser.run_report import SourceFetchResult, SourceHealth, SourceStatus


SNAPSHOT_FILENAME = "rts_last_good.json"
STATE_FILENAME = "rts_background_state.json"
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 168
HEALTHY_STATUSES = {"ok", "empty"}
KNOWN_RTS_SOURCES = (
    "rts-rosatom",
    "rts-zakupki-simferopol",
    "rts-yalta-zmo",
    "rts-market",
)


class ReportAwareSource(Protocol):
    def fetch_with_report(self, keywords: Iterable[str]) -> SourceFetchResult:
        ...


@dataclass(frozen=True)
class RtsRefreshOutcome:
    status: str
    exit_code: int
    fetched_count: int
    snapshot_count: int
    preserved_count: int
    detail: str


class RtsSnapshotStore:
    """Atomic last-good storage shared by the isolated RTS and daily fast cycles."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.snapshot_path = data_dir / SNAPSHOT_FILENAME
        self.state_path = data_dir / STATE_FILENAME

    def refresh(
        self,
        source: ReportAwareSource,
        keywords: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> RtsRefreshOutcome:
        attempted_at = now or datetime.now()
        started_at = monotonic()
        (
            previous,
            previous_generated_at,
            previous_sources,
            previous_source_generated_at,
        ) = self._read_snapshot()

        try:
            result = source.fetch_with_report(keywords)
        except Exception as exc:
            detail = f"RTS refresh failed before a report was produced: {exc.__class__.__name__}: {exc}"
            outcome = RtsRefreshOutcome(
                status="error",
                exit_code=2,
                fetched_count=0,
                snapshot_count=len(previous),
                preserved_count=len(previous),
                detail=detail,
            )
            self._write_state(outcome, [], attempted_at, previous_generated_at, monotonic() - started_at)
            return outcome

        merged, preserved_count, suspicious_empty = _merge_with_previous(
            previous,
            result,
        )
        hard_failures = [
            item
            for item in result.health
            if item.status not in HEALTHY_STATUSES
        ]
        meaningful_response = bool(result.tenders) or any(
            item.status in HEALTHY_STATUSES for item in result.health
        )
        all_empty = bool(result.health) and not result.tenders and all(
            item.status == "empty" for item in result.health
        )

        # Never replace a useful last-good file with an all-empty or all-failed pass.
        can_write_snapshot = meaningful_response and not (all_empty and previous)
        if can_write_snapshot:
            source_generated_at = _updated_source_generated_at(
                previous,
                previous_source_generated_at,
                result,
                attempted_at,
            )
            source_names = {
                *previous_sources,
                *(item.source for item in result.health),
                *(item.source for item in merged),
                *source_generated_at,
            }
            self._write_snapshot(
                merged,
                attempted_at,
                source_names,
                source_generated_at,
            )
            snapshot_generated_at = attempted_at
        else:
            snapshot_generated_at = previous_generated_at

        if not result.health or not meaningful_response:
            status = "error"
            detail = "RTS did not return a usable endpoint response; last-good snapshot preserved"
        elif all_empty and previous:
            status = "suspect_empty"
            detail = "RTS unexpectedly returned zero rows; non-empty last-good snapshot preserved"
        elif hard_failures or result.errors or suspicious_empty:
            status = "partial"
            details = [
                "successful endpoints updated; failed or suspiciously empty endpoints kept from last-good"
            ]
            if result.errors:
                details.append("; ".join(result.errors))
            detail = "; ".join(details)
        else:
            status = "ok"
            detail = "all RTS endpoints completed; atomic snapshot updated"

        exit_code = 0 if status == "ok" else 2
        outcome = RtsRefreshOutcome(
            status=status,
            exit_code=exit_code,
            fetched_count=len(result.tenders),
            snapshot_count=len(merged) if can_write_snapshot else len(previous),
            preserved_count=preserved_count if can_write_snapshot else len(previous),
            detail=detail,
        )
        self._write_state(
            outcome,
            result.health,
            attempted_at,
            snapshot_generated_at,
            monotonic() - started_at,
        )
        return outcome

    def load_for_fast_run(
        self,
        *,
        now: datetime | None = None,
        max_age_hours: int = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    ) -> SourceFetchResult:
        started_at = monotonic()
        if not self.snapshot_path.exists():
            return _unavailable_snapshot_result(
                "skipped",
                started_at,
                "background snapshot has not been created yet",
            )
        try:
            (
                tenders,
                generated_at,
                source_names,
                source_generated_at,
            ) = self._read_snapshot(strict=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            detail = f"cannot read RTS background snapshot: {exc.__class__.__name__}: {exc}"
            result = _unavailable_snapshot_result("error", started_at, detail)
            result.errors.append(detail)
            return result

        current = now or datetime.now()
        if generated_at is None:
            return _unavailable_snapshot_result(
                "error", started_at, "RTS background snapshot has no generated_at"
            )
        return self._loaded_snapshot_result(
            tenders,
            source_names,
            source_generated_at,
            current,
            max_age_hours,
            started_at,
        )

    def _read_snapshot(
        self,
        *,
        strict: bool = False,
    ) -> tuple[
        list[TenderRecord],
        datetime | None,
        list[str],
        dict[str, datetime | None],
    ]:
        if not self.snapshot_path.exists():
            return [], None, list(KNOWN_RTS_SOURCES), {}
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("invalid RTS snapshot structure")
            generated_at = _parse_datetime(payload.get("generated_at"))
            records = [_record_from_dict(item) for item in payload["items"]]
            source_values = payload.get("sources", [])
            source_names = (
                [item for item in source_values if isinstance(item, str) and item]
                if isinstance(source_values, list)
                else []
            )
            source_names = sorted(
                {*KNOWN_RTS_SOURCES, *source_names, *(item.source for item in records)}
            )
            raw_source_generated_at = payload.get("source_generated_at")
            if raw_source_generated_at is None:
                # Version 1 snapshots only had one global timestamp. Treat it
                # as the last-good time for every recorded source so an upgrade
                # does not unexpectedly discard a still-fresh snapshot.
                source_generated_at = {
                    source_name: generated_at for source_name in source_names
                }
            elif isinstance(raw_source_generated_at, dict):
                source_generated_at = {}
                for source_name, value in raw_source_generated_at.items():
                    if not isinstance(source_name, str) or not source_name:
                        raise ValueError("source_generated_at keys must be source names")
                    source_generated_at[source_name] = _parse_datetime(value)
                source_names = sorted({*source_names, *source_generated_at})
            else:
                raise ValueError("source_generated_at must be an object")
            return records, generated_at, source_names, source_generated_at
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            if strict:
                raise
            return [], None, list(KNOWN_RTS_SOURCES), {}

    def _write_snapshot(
        self,
        tenders: list[TenderRecord],
        generated_at: datetime,
        source_names: Iterable[str],
        source_generated_at: dict[str, datetime | None],
    ) -> None:
        payload = {
            "version": 2,
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "count": len(tenders),
            "sources": sorted({*KNOWN_RTS_SOURCES, *source_names}),
            "source_generated_at": {
                source_name: value.isoformat(timespec="seconds")
                for source_name, value in sorted(source_generated_at.items())
                if value is not None
            },
            "items": [_record_to_dict(tender) for tender in tenders],
        }
        _atomic_json_write(self.snapshot_path, payload)

    def _loaded_snapshot_result(
        self,
        tenders: list[TenderRecord],
        source_names: list[str],
        source_generated_at: dict[str, datetime | None],
        current_time: datetime,
        max_age_hours: int,
        started_at: float,
    ) -> SourceFetchResult:
        max_age = timedelta(hours=max_age_hours)
        fresh_sources = {
            source_name
            for source_name, generated_at in source_generated_at.items()
            if generated_at is not None and current_time - generated_at <= max_age
        }
        fresh_tenders = [item for item in tenders if item.source in fresh_sources]
        counts = {
            source: sum(item.source == source for item in fresh_tenders)
            for source in source_names
        }
        state = self._read_state()
        state_sources = state.get("sources") if isinstance(state, dict) else None
        state_by_source = {
            str(item.get("source")): item
            for item in state_sources
            if isinstance(item, dict) and isinstance(item.get("source"), str)
        } if isinstance(state_sources, list) else {}
        overall_status = str(state.get("status", "")) if isinstance(state, dict) else ""
        overall_detail = str(state.get("detail", "")) if isinstance(state, dict) else ""

        health: list[SourceHealth] = []
        for source_name in source_names:
            latest = state_by_source.get(source_name)
            generated_at = source_generated_at.get(source_name)
            if generated_at is None:
                latest_status = str(latest.get("status", "")) if latest else ""
                status = (
                    cast(SourceStatus, latest_status)
                    if latest_status in {
                        "partial",
                        "blocked",
                        "timeout",
                        "ssl_error",
                        "error",
                    }
                    else "skipped"
                )
                detail = "source has no successful background snapshot yet"
                if latest and latest.get("detail"):
                    detail = f"{detail}; {latest['detail']}"
                found = 0
            elif source_name not in fresh_sources:
                age_hours = (current_time - generated_at).total_seconds() / 3600
                status = "skipped"
                detail = (
                    f"source snapshot is stale ({age_hours:.1f} h; limit "
                    f"{max_age_hours} h), source rows were not imported"
                )
                if latest and latest.get("detail"):
                    detail = f"{detail}; latest refresh: {latest['detail']}"
                found = 0
            elif latest:
                status = cast(SourceStatus, latest.get("status", "error"))
                detail = str(latest.get("detail", "") or _source_snapshot_detail(generated_at))
                found = (
                    counts[source_name]
                    if status in {"ok", "empty"}
                    else int(latest.get("found", counts[source_name]))
                )
            elif overall_status and overall_status != "ok":
                status = "error"
                detail = (
                    f"{overall_detail}; {_source_snapshot_detail(generated_at)}"
                ).strip("; ")
                found = 0
            else:
                status = "ok" if counts[source_name] else "empty"
                detail = _source_snapshot_detail(generated_at)
                found = counts[source_name]
            health.append(
                SourceHealth(
                    source=source_name,
                    status=status,
                    found=found,
                    elapsed_seconds=round(monotonic() - started_at, 3),
                    detail=detail,
                )
            )
        return SourceFetchResult(tenders=fresh_tenders, health=health)

    def _read_state(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_state(
        self,
        outcome: RtsRefreshOutcome,
        health: list[SourceHealth],
        attempted_at: datetime,
        snapshot_generated_at: datetime | None,
        elapsed_seconds: float,
    ) -> None:
        previous_state: dict[str, object] = {}
        if self.state_path.exists():
            try:
                loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    previous_state = loaded
            except (OSError, ValueError):
                pass
        last_success_at = previous_state.get("last_success_at")
        if outcome.status == "ok":
            last_success_at = attempted_at.isoformat(timespec="seconds")
        payload = {
            "last_attempt_at": attempted_at.isoformat(timespec="seconds"),
            "last_success_at": last_success_at,
            "snapshot_generated_at": (
                snapshot_generated_at.isoformat(timespec="seconds")
                if snapshot_generated_at
                else None
            ),
            "status": outcome.status,
            "exit_code": outcome.exit_code,
            "fetched_count": outcome.fetched_count,
            "snapshot_count": outcome.snapshot_count,
            "preserved_count": outcome.preserved_count,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "detail": outcome.detail,
            "sources": [asdict(item) for item in health],
        }
        _atomic_json_write(self.state_path, payload)


def _merge_with_previous(
    previous: list[TenderRecord],
    current: SourceFetchResult,
) -> tuple[list[TenderRecord], int, bool]:
    previous_by_source: dict[str, list[TenderRecord]] = {}
    current_by_source: dict[str, list[TenderRecord]] = {}
    for record in previous:
        previous_by_source.setdefault(record.source, []).append(record)
    for record in current.tenders:
        current_by_source.setdefault(record.source, []).append(record)

    health_by_source = {item.source: item for item in current.health}
    merged: list[TenderRecord] = []
    preserved_count = 0
    suspicious_empty = False
    for source_name in sorted(set(previous_by_source) | set(current_by_source) | set(health_by_source)):
        old_rows = previous_by_source.get(source_name, [])
        new_rows = current_by_source.get(source_name, [])
        health = health_by_source.get(source_name)
        status = health.status if health else "error"
        if status == "ok":
            if new_rows or not old_rows:
                merged.extend(new_rows)
            else:
                # A nominally successful endpoint suddenly returning no rows
                # is not enough evidence to erase a non-empty last-good segment.
                suspicious_empty = True
                preserved_count += len(old_rows)
                merged.extend(old_rows)
        elif status == "empty":
            if old_rows:
                suspicious_empty = True
                preserved_count += len(old_rows)
                merged.extend(old_rows)
        elif status == "partial":
            combined = _prefer_new_rows(old_rows, new_rows)
            preserved_count += max(0, len(combined) - len(new_rows))
            merged.extend(combined)
        else:
            preserved_count += len(old_rows)
            merged.extend(old_rows)

    return _dedupe_snapshot(merged), preserved_count, suspicious_empty


def _updated_source_generated_at(
    previous: list[TenderRecord],
    previous_generated_at: dict[str, datetime | None],
    current: SourceFetchResult,
    attempted_at: datetime,
) -> dict[str, datetime | None]:
    """Advance freshness only when a source segment was genuinely refreshed.

    A failed source keeps its old timestamp. A partial response containing old
    preserved rows also keeps the old timestamp, because a single per-source
    timestamp must conservatively describe the oldest rows in that segment.
    """
    result = dict(previous_generated_at)
    previous_by_source: dict[str, list[TenderRecord]] = {}
    current_by_source: dict[str, list[TenderRecord]] = {}
    for record in previous:
        previous_by_source.setdefault(record.source, []).append(record)
    for record in current.tenders:
        current_by_source.setdefault(record.source, []).append(record)

    for health in current.health:
        old_rows = previous_by_source.get(health.source, [])
        new_rows = current_by_source.get(health.source, [])
        if health.status == "ok" and (new_rows or not old_rows):
            result[health.source] = attempted_at
        elif health.status == "empty" and not old_rows:
            # A confirmed empty source has no retained records to age, but its
            # successful check should still remain visible as fresh health.
            result[health.source] = attempted_at
        elif health.status == "partial" and new_rows and not old_rows:
            # There is no older segment to preserve: the newly observed rows
            # are useful, even though Task Scheduler will retry the source.
            result[health.source] = attempted_at
    return result


def _source_snapshot_detail(generated_at: datetime) -> str:
    return f"last-good source snapshot from {generated_at.isoformat(timespec='seconds')}"


def _prefer_new_rows(
    previous: list[TenderRecord], current: list[TenderRecord]
) -> list[TenderRecord]:
    rows = {_snapshot_key(item): item for item in previous}
    rows.update({_snapshot_key(item): item for item in current})
    return list(rows.values())


def _dedupe_snapshot(tenders: list[TenderRecord]) -> list[TenderRecord]:
    rows: dict[str, TenderRecord] = {}
    for tender in tenders:
        rows[_snapshot_key(tender)] = tender
    return sorted(rows.values(), key=lambda item: (item.source, item.tender_number or "", item.url))


def _snapshot_key(tender: TenderRecord) -> str:
    return f"{tender.source}:{tender.tender_number}" if tender.tender_number else tender.unique_key


def _unavailable_snapshot_result(
    status: str,
    started_at: float,
    detail: str,
) -> SourceFetchResult:
    return SourceFetchResult(
        tenders=[],
        health=[
            SourceHealth(
                source=source_name,
                status=cast(SourceStatus, status),
                found=0,
                elapsed_seconds=round(monotonic() - started_at, 3),
                detail=detail,
            )
            for source_name in KNOWN_RTS_SOURCES
        ],
    )


def _record_to_dict(tender: TenderRecord) -> dict[str, object]:
    payload = asdict(tender)
    for field_name in ("deadline", "published_at", "discovered_at"):
        value = payload[field_name]
        payload[field_name] = value.isoformat(timespec="seconds") if value else None
    return payload


def _record_from_dict(value: object) -> TenderRecord:
    if not isinstance(value, dict):
        raise ValueError("RTS snapshot item must be an object")
    title = value.get("title")
    url = value.get("url")
    source = value.get("source")
    if not all(isinstance(item, str) and item for item in (title, url, source)):
        raise ValueError("RTS snapshot item requires title, url and source")
    return TenderRecord(
        title=cast(str, title),
        url=cast(str, url),
        source=cast(str, source),
        tender_number=_optional_str(value.get("tender_number")),
        customer=_optional_str(value.get("customer")),
        region=_optional_str(value.get("region")),
        price=_optional_float(value.get("price")),
        deadline=_parse_datetime(value.get("deadline")),
        status=_optional_str(value.get("status")),
        published_at=_parse_datetime(value.get("published_at")),
        discovered_at=_parse_datetime(value.get("discovered_at")),
        raw_text=_optional_str(value.get("raw_text")) or "",
        category=_optional_str(value.get("category")),
        include_reason=_optional_str(value.get("include_reason")) or "",
        exclude_reason=_optional_str(value.get("exclude_reason")) or "",
        filter_status=cast(FilterStatus, value.get("filter_status") or "excluded"),
        match_confidence=cast(MatchConfidence | None, value.get("match_confidence")),
        review_priority=cast(ReviewPriority | None, value.get("review_priority")),
        matched_terms=_string_list(value.get("matched_terms")),
        detail_status=cast(DetailStatus, value.get("detail_status") or "not_checked"),
        document_matches=_string_list(value.get("document_matches")),
        delivery_region_evidence=_optional_str(value.get("delivery_region_evidence")) or "",
        source_confidence=_optional_float(value.get("source_confidence")) or 0.0,
    )


def _parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("datetime value must be a string")
    return datetime.fromisoformat(value)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("numeric value expected")
    return float(value)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
