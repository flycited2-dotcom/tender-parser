import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from tender_parser.models import TenderRecord
from tender_parser.storage import TenderStorage


def test_storage_upserts_without_duplicates(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    tender = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        price=45_000.0,
        deadline=datetime(2026, 5, 25, 10, 0),
        filter_status="matched",
        include_reason="ok",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )

    first_seen = storage.upsert_many([tender])
    second_seen = storage.upsert_many([tender])

    rows = storage.fetch_by_status("matched")
    assert len(rows) == 1
    assert rows[0].title == "Поставка МФУ"
    assert first_seen == [tender]
    assert second_seen == []


def test_storage_round_trips_match_confidence(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    tender = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        price=45_000.0,
        deadline=datetime(2026, 5, 25, 10, 0),
        filter_status="matched",
        match_confidence="точное",
        review_priority="hot",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )

    storage.upsert_many([tender])

    loaded = storage.fetch_by_status("matched")
    assert loaded[0].match_confidence == "точное"
    assert loaded[0].review_priority == "hot"


def test_storage_keeps_filled_fields_when_update_is_empty(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    full = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        customer="Заказчик",
        region="Республика Крым",
        price=45_000.0,
        deadline=datetime(2026, 5, 25, 10, 0),
        filter_status="matched",
        review_priority="hot",
        discovered_at=datetime(2026, 5, 19, 12, 0),
        raw_text="Поставка МФУ в Республику Крым",
    )
    degraded = replace(
        full,
        customer=None,
        region=None,
        price=None,
        deadline=None,
        raw_text="",
        filter_status="review",
        review_priority="review",
    )

    storage.upsert_many([full])
    storage.upsert_many([degraded])

    row = storage.fetch_by_status("review")[0]
    assert row.customer == "Заказчик"
    assert row.region == "Республика Крым"
    assert row.price == 45_000.0
    assert row.deadline == datetime(2026, 5, 25, 10, 0)
    assert row.raw_text == "Поставка МФУ в Республику Крым"
    assert row.review_priority == "review"


def test_storage_reports_promotion_to_actionable(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    excluded = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        filter_status="excluded",
        review_priority="excluded",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )

    assert storage.upsert_many([excluded]) == [excluded]

    promoted = replace(excluded, filter_status="matched", review_priority="hot", price=45_000.0)
    assert storage.upsert_many([promoted]) == [promoted]
    assert storage.upsert_many([promoted]) == []


def test_storage_keeps_document_evidence_on_degraded_rerun(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    checked = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        filter_status="matched",
        review_priority="hot",
        detail_status="documents_checked",
        document_matches=["мфу", "симферополь"],
        delivery_region_evidence="notice.pdf: regions=симферополь",
        source_confidence=0.9,
    )
    degraded = replace(
        checked,
        detail_status="not_checked",
        document_matches=[],
        delivery_region_evidence="",
        source_confidence=0.0,
    )

    storage.upsert_many([checked])
    storage.upsert_many([degraded])

    row = storage.fetch_by_status("matched")[0]
    assert row.detail_status == "documents_checked"
    assert row.document_matches == ["мфу", "симферополь"]
    assert row.delivery_region_evidence == "notice.pdf: regions=симферополь"
    assert row.source_confidence == 0.9


def test_preview_new_does_not_write(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    tender = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        filter_status="matched",
        review_priority="hot",
    )

    first = storage.preview_new([tender])
    second = storage.preview_new([tender])

    assert first == [tender]
    assert second == [tender]
    assert storage.fetch_by_status("matched") == []

    storage.upsert_many([tender])
    assert storage.preview_new([tender]) == []


def test_storage_uses_wal_and_closes_connections(tmp_path: Path) -> None:
    import gc
    import sqlite3

    db_path = tmp_path / "tenders.db"
    storage = TenderStorage(db_path)
    storage.upsert_many([])
    gc.disable()
    try:
        renamed = db_path.with_name("moved.db")
        db_path.rename(renamed)  # упадёт WinError 32, если соединение не закрыто
        renamed.rename(db_path)
    finally:
        gc.enable()
    with sqlite3.connect(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_storage_migrates_legacy_database_for_match_confidence(tmp_path: Path) -> None:
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
                filter_status TEXT NOT NULL
            )
            """
        )

    TenderStorage(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tenders)")}
    assert "match_confidence" in columns
    assert "review_priority" in columns
