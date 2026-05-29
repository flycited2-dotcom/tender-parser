import json
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from tender_parser.exporters.excel import export_excel
from tender_parser.exporters.json_exporter import export_json
from tender_parser.models import TenderRecord


def make_tender(status: str) -> TenderRecord:
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
        category="Компьютерная техника и периферия" if status != "excluded" else None,
        include_reason="ok" if status != "excluded" else "",
        exclude_reason="" if status == "matched" else "регион не найден",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )


def test_export_excel_creates_expected_sheets(tmp_path: Path) -> None:
    output = tmp_path / "tenders.xlsx"
    export_excel(
        [make_tender("matched")],
        [make_tender("review")],
        [make_tender("excluded")],
        output,
    )

    workbook = load_workbook(output)
    assert workbook.sheetnames == ["Подходящие", "На проверку", "Отсеянные"]
    assert workbook["Подходящие"]["C2"].value == "Поставка МФУ"
    assert workbook["На проверку"]["C2"].value == "Поставка МФУ"


def test_export_json_writes_matched_tenders(tmp_path: Path) -> None:
    output = tmp_path / "latest.json"
    export_json([make_tender("matched")], output)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["items"][0]["title"] == "Поставка МФУ"
    assert data["items"][0]["filter_status"] == "matched"
