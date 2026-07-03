import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from tender_parser.cli import run
from tender_parser.models import TenderRecord
from tender_parser.rts_accumulator import RtsAccumulator, RtsAccumulatorSource
from tender_parser.run_report import SourceFetchResult, SourceHealth


def make_tender(number: str = "3942869", **overrides: object) -> TenderRecord:
    data = {
        "title": "Поставка МФУ для Крымэнерго",
        "url": f"https://223.rts-tender.ru/supplier/auction/Trade/View.aspx?Id={number}",
        "source": "rts-cabinet",
        "tender_number": number,
        "customer": "ГУП РК Крымэнерго",
        "region": "Респ. Крым",
        "price": 180_000.0,
        "deadline": datetime(2026, 7, 10, 10, 0),
        "raw_text": "Поставка МФУ для Крымэнерго Респ. Крым",
        "detail_status": "enriched",
        "source_confidence": 0.9,
    }
    data.update(overrides)
    return TenderRecord(**data)


def test_accumulator_adds_and_deduplicates_by_unique_key(tmp_path: Path) -> None:
    accumulator = RtsAccumulator(tmp_path / "tenders.db")

    added, total = accumulator.add_many([make_tender(), make_tender("4000001")])
    assert (added, total) == (2, 2)

    added, total = accumulator.add_many([make_tender(), make_tender("4000002")])
    assert (added, total) == (1, 3)

    records = accumulator.load_all()
    assert len(records) == 3
    assert {record.tender_number for record in records} == {"3942869", "4000001", "4000002"}


def test_accumulator_keeps_filled_fields_on_repeat(tmp_path: Path) -> None:
    accumulator = RtsAccumulator(tmp_path / "tenders.db")
    accumulator.add_many([make_tender()])
    accumulator.add_many([make_tender(price=None, region=None)])

    record = accumulator.load_all()[0]
    assert record.price == 180_000.0
    assert record.region == "Респ. Крым"


def test_accumulator_round_trips_record_fields(tmp_path: Path) -> None:
    accumulator = RtsAccumulator(tmp_path / "tenders.db")
    accumulator.add_many([make_tender()])

    record = accumulator.load_all()[0]
    assert record.title == "Поставка МФУ для Крымэнерго"
    assert record.deadline == datetime(2026, 7, 10, 10, 0)
    assert record.source == "rts-cabinet"
    assert record.detail_status == "enriched"
    assert record.source_confidence == 0.9


class FakeCabinet:
    def __init__(self, tenders: list[TenderRecord], errors: list[str] | None = None) -> None:
        self.tenders = tenders
        self.errors = errors or []

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return self.tenders

    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        return SourceFetchResult(
            tenders=self.tenders,
            health=[SourceHealth(source="rts-cabinet", status="ok", found=len(self.tenders), elapsed_seconds=0.1)],
            errors=self.errors,
        )


def test_rts_add_page_command_accumulates_pages(tmp_path: Path, capsys) -> None:
    first = run(["rts-add-page", "--base-dir", str(tmp_path)], source=FakeCabinet([make_tender()]))
    assert first == 0

    second = run(
        ["rts-add-page", "--base-dir", str(tmp_path)],
        source=FakeCabinet([make_tender(), make_tender("4000001")]),
    )
    assert second == 0
    output = capsys.readouterr().out
    assert "Новых в накопителе: 1" in output
    assert "Всего в накопителе RTS: 2" in output


def test_rts_add_page_command_fails_without_cabinet(tmp_path: Path) -> None:
    result = run(
        ["rts-add-page", "--base-dir", str(tmp_path)],
        source=FakeCabinet([], errors=["chrome unavailable"]),
    )

    assert result == 2
    assert RtsAccumulator(tmp_path / "data" / "tenders.db").load_all() == []


def test_run_profile_rts_accumulated_exports_accumulated_tenders(tmp_path: Path) -> None:
    run(["rts-add-page", "--base-dir", str(tmp_path)], source=FakeCabinet([make_tender()]))

    result = run(
        ["--base-dir", str(tmp_path), "--profile", "rts-accumulated", "--now", "2026-07-03T12:00:00"]
    )

    assert result == 0
    data = json.loads((tmp_path / "exports" / "latest.json").read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["items"][0]["tender_number"] == "3942869"
    assert data["items"][0]["review_priority"] == "hot"


def test_accumulator_source_reads_from_db(tmp_path: Path) -> None:
    RtsAccumulator(tmp_path / "tenders.db").add_many([make_tender()])

    tenders = RtsAccumulatorSource(tmp_path / "tenders.db").fetch_keywords([])

    assert len(tenders) == 1
    assert tenders[0].tender_number == "3942869"
