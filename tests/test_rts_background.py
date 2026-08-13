from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from tender_parser.models import TenderRecord
from tender_parser.rts_background import RtsSnapshotStore
from tender_parser.run_report import SourceFetchResult, SourceHealth


NOW = datetime(2026, 8, 13, 2, 30)


def _record(source: str, number: str, title: str = "Поставка МФУ") -> TenderRecord:
    return TenderRecord(
        title=title,
        url=f"https://{source}.example.test/{number}",
        source=source,
        tender_number=number,
        region="Республика Крым",
        raw_text=title,
    )


class ReportSource:
    def __init__(self, result: SourceFetchResult) -> None:
        self.result = result

    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        return self.result


def _health(source: str, status: str, found: int) -> SourceHealth:
    return SourceHealth(source=source, status=status, found=found, elapsed_seconds=0.1)  # type: ignore[arg-type]


def test_successful_refresh_writes_atomic_snapshot_for_fast_run(tmp_path: Path) -> None:
    record = _record("rts-market", "RTS-1")
    store = RtsSnapshotStore(tmp_path)

    outcome = store.refresh(
        ReportSource(
            SourceFetchResult(tenders=[record], health=[_health("rts-market", "ok", 1)])
        ),
        ["мфу"],
        now=NOW,
    )
    loaded = store.load_for_fast_run(now=NOW + timedelta(hours=2))

    assert outcome.status == "ok"
    assert outcome.exit_code == 0
    assert [item.tender_number for item in loaded.tenders] == ["RTS-1"]
    health = {item.source: item for item in loaded.health}
    assert health["rts-market"].status == "ok"
    assert health["rts-market"].found == 1
    assert set(health) >= {
        "rts-rosatom",
        "rts-zakupki-simferopol",
        "rts-yalta-zmo",
        "rts-market",
    }
    assert not (tmp_path / "rts_last_good.json.tmp").exists()


def test_failed_endpoint_preserves_that_endpoints_previous_rows(tmp_path: Path) -> None:
    store = RtsSnapshotStore(tmp_path)
    first = SourceFetchResult(
        tenders=[_record("rts-a", "A-1"), _record("rts-b", "B-1")],
        health=[_health("rts-a", "ok", 1), _health("rts-b", "ok", 1)],
    )
    assert store.refresh(ReportSource(first), [], now=NOW).exit_code == 0

    partial = SourceFetchResult(
        tenders=[_record("rts-a", "A-2")],
        health=[_health("rts-a", "ok", 1), _health("rts-b", "blocked", 0)],
        errors=["rts-b: captcha"],
    )
    outcome = store.refresh(ReportSource(partial), [], now=NOW + timedelta(days=1))
    loaded = store.load_for_fast_run(now=NOW + timedelta(days=1, hours=1))

    assert outcome.status == "partial"
    assert outcome.exit_code == 2
    assert outcome.preserved_count == 1
    assert {(item.source, item.tender_number) for item in loaded.tenders} == {
        ("rts-a", "A-2"),
        ("rts-b", "B-1"),
    }


def test_failed_endpoint_does_not_inherit_successful_endpoints_freshness(tmp_path: Path) -> None:
    store = RtsSnapshotStore(tmp_path)
    initial = SourceFetchResult(
        tenders=[_record("rts-a", "A-1"), _record("rts-b", "B-1")],
        health=[_health("rts-a", "ok", 1), _health("rts-b", "ok", 1)],
    )
    store.refresh(ReportSource(initial), [], now=NOW)

    refreshed_at = NOW + timedelta(hours=100)
    partial = SourceFetchResult(
        tenders=[_record("rts-a", "A-2")],
        health=[_health("rts-a", "ok", 1), _health("rts-b", "blocked", 0)],
        errors=["rts-b: captcha"],
    )
    store.refresh(ReportSource(partial), [], now=refreshed_at)
    payload = json.loads((tmp_path / "rts_last_good.json").read_text(encoding="utf-8"))

    assert payload["version"] == 2
    assert payload["source_generated_at"]["rts-a"] == refreshed_at.isoformat(
        timespec="seconds"
    )
    assert payload["source_generated_at"]["rts-b"] == NOW.isoformat(
        timespec="seconds"
    )

    loaded = store.load_for_fast_run(
        now=NOW + timedelta(hours=170),
        max_age_hours=168,
    )
    health = {item.source: item for item in loaded.health}

    assert {(item.source, item.tender_number) for item in loaded.tenders} == {
        ("rts-a", "A-2")
    }
    assert health["rts-a"].status == "ok"
    assert health["rts-b"].status == "skipped"
    assert health["rts-b"].found == 0
    assert "source snapshot is stale" in health["rts-b"].detail


def test_partial_segment_with_preserved_rows_keeps_old_freshness(tmp_path: Path) -> None:
    store = RtsSnapshotStore(tmp_path)
    store.refresh(
        ReportSource(
            SourceFetchResult(
                tenders=[_record("rts-a", "A-1")],
                health=[_health("rts-a", "ok", 1)],
            )
        ),
        [],
        now=NOW,
    )
    store.refresh(
        ReportSource(
            SourceFetchResult(
                tenders=[_record("rts-a", "A-2")],
                health=[_health("rts-a", "partial", 1)],
                errors=["rts-a: later query timed out"],
            )
        ),
        [],
        now=NOW + timedelta(hours=100),
    )
    payload = json.loads((tmp_path / "rts_last_good.json").read_text(encoding="utf-8"))

    assert payload["source_generated_at"]["rts-a"] == NOW.isoformat(
        timespec="seconds"
    )
    loaded = store.load_for_fast_run(
        now=NOW + timedelta(hours=170),
        max_age_hours=168,
    )
    health = {item.source: item for item in loaded.health}
    assert not [item for item in loaded.tenders if item.source == "rts-a"]
    assert health["rts-a"].status == "skipped"


def test_same_tender_number_from_different_rts_sources_is_not_collapsed(tmp_path: Path) -> None:
    store = RtsSnapshotStore(tmp_path)
    result = SourceFetchResult(
        tenders=[_record("rts-a", "42"), _record("rts-b", "42")],
        health=[_health("rts-a", "ok", 1), _health("rts-b", "ok", 1)],
    )

    outcome = store.refresh(ReportSource(result), [], now=NOW)
    loaded = store.load_for_fast_run(now=NOW)

    assert outcome.snapshot_count == 2
    assert {(item.source, item.tender_number) for item in loaded.tenders} >= {
        ("rts-a", "42"),
        ("rts-b", "42"),
    }


def test_all_empty_refresh_does_not_erase_nonempty_last_good(tmp_path: Path) -> None:
    store = RtsSnapshotStore(tmp_path)
    initial = SourceFetchResult(
        tenders=[_record("rts-market", "RTS-1")],
        health=[_health("rts-market", "ok", 1)],
    )
    store.refresh(ReportSource(initial), [], now=NOW)
    before = (tmp_path / "rts_last_good.json").read_text(encoding="utf-8")

    outcome = store.refresh(
        ReportSource(
            SourceFetchResult(health=[_health("rts-market", "empty", 0)])
        ),
        [],
        now=NOW + timedelta(days=1),
    )

    assert outcome.status == "suspect_empty"
    assert outcome.exit_code == 2
    assert (tmp_path / "rts_last_good.json").read_text(encoding="utf-8") == before


def test_exception_preserves_last_good_and_records_failed_state(tmp_path: Path) -> None:
    class RaisingSource:
        def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
            raise TimeoutError("too slow")

    store = RtsSnapshotStore(tmp_path)
    store.refresh(
        ReportSource(
            SourceFetchResult(
                tenders=[_record("rts-market", "RTS-1")],
                health=[_health("rts-market", "ok", 1)],
            )
        ),
        [],
        now=NOW,
    )
    outcome = store.refresh(RaisingSource(), [], now=NOW + timedelta(days=1))
    state = json.loads((tmp_path / "rts_background_state.json").read_text(encoding="utf-8"))

    assert outcome.status == "error"
    assert outcome.preserved_count == 1
    assert state["status"] == "error"
    assert state["last_success_at"] == NOW.isoformat(timespec="seconds")


def test_stale_or_corrupt_snapshot_is_not_merged_into_fast_run(tmp_path: Path) -> None:
    store = RtsSnapshotStore(tmp_path)
    store.refresh(
        ReportSource(
            SourceFetchResult(
                tenders=[_record("rts-market", "RTS-1")],
                health=[_health("rts-market", "ok", 1)],
            )
        ),
        [],
        now=NOW,
    )

    stale = store.load_for_fast_run(now=NOW + timedelta(hours=169), max_age_hours=168)
    assert stale.tenders == []
    assert {item.status for item in stale.health} == {"skipped"}

    (tmp_path / "rts_last_good.json").write_text("not json", encoding="utf-8")
    corrupt = store.load_for_fast_run(now=NOW)
    assert corrupt.tenders == []
    assert {item.status for item in corrupt.health} == {"error"}
    assert corrupt.errors


def test_version_one_snapshot_uses_global_timestamp_for_backward_compatibility(
    tmp_path: Path,
) -> None:
    store = RtsSnapshotStore(tmp_path)
    store.refresh(
        ReportSource(
            SourceFetchResult(
                tenders=[_record("rts-market", "RTS-1")],
                health=[_health("rts-market", "ok", 1)],
            )
        ),
        [],
        now=NOW,
    )
    payload = json.loads((tmp_path / "rts_last_good.json").read_text(encoding="utf-8"))
    payload["version"] = 1
    payload.pop("source_generated_at")
    (tmp_path / "rts_last_good.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    fresh = store.load_for_fast_run(now=NOW + timedelta(hours=2))
    stale = store.load_for_fast_run(
        now=NOW + timedelta(hours=169),
        max_age_hours=168,
    )

    assert [item.tender_number for item in fresh.tenders] == ["RTS-1"]
    assert stale.tenders == []
    assert {item.status for item in stale.health} == {"skipped"}
