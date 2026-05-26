from datetime import datetime
from pathlib import Path

from tender_parser.cli import run
from tender_parser.models import TenderRecord


class FakeSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return [
            TenderRecord(
                title="Поставка МФУ в Республику Крым",
                url="https://example.test/tender-1/",
                source="fake",
                tender_number="1",
                customer="Заказчик",
                region="Республика Крым",
                price=45_000.0,
                deadline=datetime(2026, 5, 25, 10, 0),
                raw_text="Поставка МФУ в Республику Крым",
            )
        ]


class EmptySource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return []


def test_run_dry_mode_creates_export_dirs(tmp_path: Path) -> None:
    result = run(["--dry-run", "--base-dir", str(tmp_path)])

    assert result == 0
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "exports").is_dir()


def test_run_with_fake_source_creates_database_and_exports(tmp_path: Path) -> None:
    result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-19T12:00:00"],
        source=FakeSource(),
    )

    assert result == 0
    assert (tmp_path / "data" / "tenders.db").exists()
    assert (tmp_path / "exports" / "latest.json").exists()
    assert list((tmp_path / "exports").glob("tenders_*.xlsx"))


def test_run_exports_only_current_run_matches(tmp_path: Path) -> None:
    first_result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-19T12:00:00"],
        source=FakeSource(),
    )
    second_result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-20T12:00:00"],
        source=EmptySource(),
    )

    assert first_result == 0
    assert second_result == 0
    assert '"count": 0' in (tmp_path / "exports" / "latest.json").read_text(encoding="utf-8")
