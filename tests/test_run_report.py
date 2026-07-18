import json
from pathlib import Path

from tender_parser.run_report import SourceHealth, flag_suspect_empty, load_previous_counts


def test_flag_suspect_empty_marks_zero_after_nonzero() -> None:
    health = [SourceHealth(source="rostender", status="empty", found=0, elapsed_seconds=1.0)]

    flagged = flag_suspect_empty(health, {"rostender": 37})

    assert flagged[0].status == "suspect_empty"
    assert "37" in flagged[0].detail


def test_flag_suspect_empty_keeps_errors_and_normal_rows() -> None:
    health = [
        SourceHealth(source="eis-zakupki", status="timeout", found=0, elapsed_seconds=1.0, detail="timeout"),
        SourceHealth(source="b2b-center", status="ok", found=5, elapsed_seconds=1.0),
        SourceHealth(source="new-source", status="empty", found=0, elapsed_seconds=1.0),
    ]

    flagged = flag_suspect_empty(health, {"eis-zakupki": 10, "b2b-center": 4})

    assert [item.status for item in flagged] == ["timeout", "ok", "empty"]


def test_load_previous_counts_reads_run_report(tmp_path: Path) -> None:
    path = tmp_path / "run_report.json"
    path.write_text(
        json.dumps({"sources": [{"source": "rostender", "found": 37}, {"source": "eis-zakupki", "found": 0}]}),
        encoding="utf-8",
    )

    assert load_previous_counts(path) == {"rostender": 37, "eis-zakupki": 0}


def test_load_previous_profile_reads_profile_field(tmp_path: Path) -> None:
    from tender_parser.run_report import load_previous_profile

    path = tmp_path / "run_report.json"
    path.write_text(json.dumps({"profile": "full", "sources": []}), encoding="utf-8")

    assert load_previous_profile(path) == "full"
    assert load_previous_profile(tmp_path / "missing.json") is None


def test_load_previous_counts_tolerates_missing_or_broken_file(tmp_path: Path) -> None:
    assert load_previous_counts(tmp_path / "missing.json") == {}

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_previous_counts(broken) == {}
