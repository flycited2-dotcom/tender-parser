import json
import sqlite3
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from tender_parser.exporters.excel import export_excel
from tender_parser.exporters.json_exporter import export_json
from tender_parser.models import TenderRecord
from tender_parser.storage import TenderStorage


def enriched_tender() -> TenderRecord:
    return TenderRecord(
        title="Поставка МФУ в Симферополь",
        url="https://example.test/tender-1/",
        source="import",
        tender_number="1",
        region="Симферополь",
        price=45_000.0,
        deadline=datetime(2026, 7, 1, 10, 0),
        filter_status="matched",
        review_priority="hot",
        detail_status="enriched",
        document_matches=["мфу", "Симферополь"],
        delivery_region_evidence="Адрес поставки: г. Симферополь",
        source_confidence=0.85,
    )


def test_json_export_includes_enrichment_fields(tmp_path: Path) -> None:
    output = tmp_path / "latest.json"

    export_json([enriched_tender()], output)

    item = json.loads(output.read_text(encoding="utf-8"))["items"][0]
    assert item["detail_status"] == "enriched"
    assert item["document_matches"] == ["мфу", "Симферополь"]
    assert item["delivery_region_evidence"] == "Адрес поставки: г. Симферополь"
    assert item["source_confidence"] == 0.85


def test_excel_export_includes_enrichment_columns(tmp_path: Path) -> None:
    output = tmp_path / "tenders.xlsx"

    export_excel([enriched_tender()], [], [], [], output)

    workbook = load_workbook(output)
    headers = [cell.value for cell in workbook["Горячие"][1]]
    row = [cell.value for cell in workbook["Горячие"][2]]
    assert "detail_status" in headers
    assert "document_matches" in headers
    assert "delivery_region_evidence" in headers
    assert "source_confidence" in headers
    assert row[headers.index("detail_status")] == "enriched"
    assert row[headers.index("document_matches")] == "мфу; Симферополь"
    assert row[headers.index("source_confidence")] == 0.85


def test_storage_round_trips_enrichment_fields(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    tender = enriched_tender()

    storage.upsert_many([tender])

    loaded = storage.fetch_by_status("matched")[0]
    assert loaded.detail_status == "enriched"
    assert loaded.document_matches == ["мфу", "Симферополь"]
    assert loaded.delivery_region_evidence == "Адрес поставки: г. Симферополь"
    assert loaded.source_confidence == 0.85


def test_storage_migrates_legacy_database_for_enrichment_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "tenders.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE tenders (
                unique_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                tender_number TEXT,
                customer TEXT,
                region TEXT,
                price REAL,
                deadline TEXT,
                status TEXT,
                published_at TEXT,
                discovered_at TEXT,
                last_seen_at TEXT,
                raw_text TEXT,
                category TEXT,
                include_reason TEXT,
                exclude_reason TEXT,
                filter_status TEXT NOT NULL,
                match_confidence TEXT,
                review_priority TEXT
            )
            """
        )

    TenderStorage(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tenders)")}
    assert "detail_status" in columns
    assert "document_matches" in columns
    assert "delivery_region_evidence" in columns
    assert "source_confidence" in columns
