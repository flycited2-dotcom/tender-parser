import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from tender_parser.exporters.excel import export_excel, sort_for_review
from tender_parser.exporters.html_report import export_html_report
from tender_parser.exporters.json_exporter import export_json, export_run_report
from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth


def make_tender(status: str) -> TenderRecord:
    priority = {"matched": "hot", "review": "review", "excluded": "excluded"}[status]
    return TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        customer="Заказчик",
        region="Республика Крым",
        price=45_000.0,
        deadline=datetime(2026, 5, 25, 10, 0),
        filter_status=status,
        review_priority=priority,
        category="Компьютерная техника и периферия" if status != "excluded" else None,
        include_reason="ok" if status != "excluded" else "",
        exclude_reason="" if status == "matched" else "регион не найден",
        match_confidence="точное" if status == "matched" else "ручная проверка",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )


def test_export_excel_creates_expected_sheets(tmp_path: Path) -> None:
    output = tmp_path / "tenders.xlsx"
    export_excel(
        [make_tender("matched")],
        [make_tender("review")],
        [replace(make_tender("review"), review_priority="wide")],
        [make_tender("excluded")],
        output,
    )

    workbook = load_workbook(output)
    assert workbook.sheetnames == ["Горячие", "На проверку", "Широкий хвост", "Отсеянные"]
    assert workbook["Горячие"]["D1"].value == "приоритет"
    assert workbook["Горячие"]["D2"].value == "hot"
    assert workbook["Горячие"]["E2"].value == "Поставка МФУ"
    assert workbook["На проверку"]["E2"].value == "Поставка МФУ"
    assert workbook["Широкий хвост"]["E2"].value == "Поставка МФУ"


def test_export_excel_adds_new_sheet_when_new_tenders_are_given(tmp_path: Path) -> None:
    output = tmp_path / "tenders.xlsx"

    export_excel(
        [make_tender("matched")],
        [],
        [],
        [],
        output,
        new_tenders=[make_tender("matched")],
    )

    workbook = load_workbook(output)
    assert workbook.sheetnames[0] == "Новые"
    assert workbook["Новые"]["E2"].value == "Поставка МФУ"


def test_sort_for_review_orders_by_priority_deadline_price_and_discovery() -> None:
    hot_late = replace(
        make_tender("matched"),
        tender_number="2",
        deadline=datetime(2026, 5, 30),
        price=100_000.0,
    )
    hot_soon = replace(
        make_tender("matched"),
        tender_number="1",
        deadline=datetime(2026, 5, 20),
        price=40_000.0,
    )
    wide = replace(
        make_tender("review"),
        tender_number="3",
        review_priority="wide",
        deadline=datetime(2026, 5, 19),
        price=1_000_000.0,
    )

    result = sort_for_review([wide, hot_late, hot_soon])

    assert [item.tender_number for item in result] == ["1", "2", "3"]


def test_export_json_writes_matched_tenders(tmp_path: Path) -> None:
    output = tmp_path / "latest.json"
    export_json([make_tender("matched")], output)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["items"][0]["title"] == "Поставка МФУ"
    assert data["items"][0]["filter_status"] == "matched"
    assert data["items"][0]["match_confidence"] == "точное"
    assert data["items"][0]["review_priority"] == "hot"


def test_export_run_report_writes_source_health(tmp_path: Path) -> None:
    output = tmp_path / "run_report.json"
    report = SourceFetchResult(
        health=[
            SourceHealth(
                source="EisZakupkiSource",
                status="ok",
                found=12,
                elapsed_seconds=1.25,
            )
        ]
    )

    export_run_report(report, output, raw_count=12, unique_count=10, new_count=3)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["summary"] == {"raw_count": 12, "unique_count": 10, "new_count": 3}
    assert data["sources"][0]["status"] == "ok"
    assert data["sources"][0]["found"] == 12


def test_export_html_report_writes_review_dashboard(tmp_path: Path) -> None:
    output = tmp_path / "latest.html"
    tender = replace(
        make_tender("matched"),
        document_matches=["мфу", "симферополь"],
        delivery_region_evidence="notice.pdf: regions=симферополь; terms=мфу",
        source_confidence=0.9,
    )
    report = SourceFetchResult(
        health=[
            SourceHealth(
                source="ImportFolderSource",
                status="ok",
                found=1,
                elapsed_seconds=0.1,
            )
        ]
    )

    export_html_report(
        [tender],
        output,
        source_report=report,
        raw_count=1,
        unique_count=1,
        new_count=1,
    )

    html = output.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "Поставка МФУ" in html
    assert "https://example.test/tender-1/" in html
    assert "notice.pdf" in html
    assert "ImportFolderSource" in html
