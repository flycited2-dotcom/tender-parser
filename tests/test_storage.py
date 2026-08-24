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


def test_storage_exposes_accumulated_history_for_regional_sheet(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    old = TenderRecord(
        title="Поставка медицинских изделий",
        url="https://example.test/old",
        source="test",
        tender_number="old",
        region="Республика Крым",
        filter_status="excluded",
        review_priority="excluded",
        exclude_reason="стоп-тема: медицина",
    )
    current = replace(old, title="Поставка МФУ", tender_number="current")
    storage.upsert_many([old, current])

    assert {item.tender_number for item in storage.fetch_all_tenders()} == {
        "old",
        "current",
    }


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
        official_number="0174100000626000005",
        official_url="https://zakupki.gov.ru/notice/0174100000626000005",
        official_source="ЕИС",
        platform_number="AST-1",
        platform_url="https://utp.sberbank-ast.ru/purchase/1",
        procurement_law="44-ФЗ",
        resolution_method="rostender-meta",
        resolution_confidence=0.98,
    )
    degraded = replace(
        full,
        customer=None,
        region=None,
        price=None,
        deadline=None,
        raw_text="",
        official_number=None,
        official_url=None,
        official_source=None,
        platform_number=None,
        platform_url=None,
        procurement_law=None,
        resolution_method=None,
        resolution_confidence=0.0,
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
    assert row.official_number == "0174100000626000005"
    assert row.official_url == "https://zakupki.gov.ru/notice/0174100000626000005"
    assert row.official_source == "ЕИС"
    assert row.platform_number == "AST-1"
    assert row.platform_url == "https://utp.sberbank-ast.ru/purchase/1"
    assert row.procurement_law == "44-ФЗ"
    assert row.resolution_method == "rostender-meta"
    assert row.resolution_confidence == 0.98


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


def test_merge_with_history_restores_known_fields_without_overwriting_fresh_values(
    tmp_path: Path,
) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    historical = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        customer="Старый заказчик",
        region="Республика Крым",
        price=45_000.0,
        deadline=datetime(2026, 5, 25, 10, 0),
        published_at=datetime(2026, 5, 19, 9, 0),
        raw_text="Полная историческая карточка",
        filter_status="matched",
        review_priority="hot",
        detail_status="documents_checked",
        document_matches=["мфу"],
        delivery_region_evidence="notice.pdf: Республика Крым",
        source_confidence=0.9,
        official_number="0174100000626000005",
        official_url="https://zakupki.gov.ru/notice/0174100000626000005",
        official_source="ЕИС",
        platform_number="AST-1",
        platform_url="https://utp.sberbank-ast.ru/purchase/1",
        procurement_law="44-ФЗ",
        resolution_method="rostender-meta",
        resolution_confidence=0.98,
    )
    storage.upsert_many([historical])

    partial = replace(
        historical,
        customer="Новый заказчик",
        region=None,
        price=None,
        deadline=None,
        published_at=None,
        raw_text="Свежая карточка",
        filter_status="excluded",
        review_priority=None,
        detail_status="not_checked",
        document_matches=[],
        delivery_region_evidence="",
        source_confidence=0.0,
        official_number=None,
        official_url=None,
        official_source=None,
        platform_number=None,
        platform_url=None,
        procurement_law=None,
        resolution_method=None,
        resolution_confidence=0.0,
    )

    merged = storage.merge_with_history([partial])[0]

    assert merged.customer == "Новый заказчик"
    assert merged.region == "Республика Крым"
    assert merged.price == 45_000.0
    assert merged.deadline == datetime(2026, 5, 25, 10, 0)
    assert merged.published_at == datetime(2026, 5, 19, 9, 0)
    assert merged.raw_text == "Свежая карточка"
    assert merged.detail_status == "documents_checked"
    assert merged.document_matches == ["мфу"]
    assert merged.delivery_region_evidence == "notice.pdf: Республика Крым"
    assert merged.source_confidence == 0.9
    assert merged.official_number == "0174100000626000005"
    assert merged.official_url == "https://zakupki.gov.ru/notice/0174100000626000005"
    assert merged.official_source == "ЕИС"
    assert merged.platform_number == "AST-1"
    assert merged.platform_url == "https://utp.sberbank-ast.ru/purchase/1"
    assert merged.procurement_law == "44-ФЗ"
    assert merged.resolution_method == "rostender-meta"
    assert merged.resolution_confidence == 0.98


def test_merge_with_history_preserves_order_and_unknown_records(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    known = TenderRecord(
        title="Поставка принтеров",
        url="https://example.test/known",
        source="test",
        tender_number="KNOWN",
        price=80_000.0,
    )
    unknown = TenderRecord(
        title="Поставка бумаги",
        url="https://example.test/unknown",
        source="test",
        tender_number="UNKNOWN",
    )
    storage.upsert_many([known])

    result = storage.merge_with_history([unknown, replace(known, price=None)])

    assert result[0] == unknown
    assert result[1].price == 80_000.0


def test_notification_outbox_retries_until_marked_sent(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    tender = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        filter_status="matched",
        review_priority="hot",
    )

    storage.upsert_many([tender], notification_candidates=[tender])
    assert [item.unique_key for item in storage.fetch_pending_notifications()] == [
        tender.unique_key
    ]

    storage.mark_notification_error([tender.unique_key], "temporary failure")
    assert [item.unique_key for item in storage.fetch_pending_notifications()] == [
        tender.unique_key
    ]

    storage.mark_notifications_sent([tender.unique_key])
    assert storage.fetch_pending_notifications() == []


def test_notification_outbox_does_not_enqueue_same_tender_twice(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    tender = TenderRecord(
        title="Поставка принтеров",
        url="https://example.test/tender-2/",
        source="test",
        tender_number="2",
        filter_status="matched",
        review_priority="hot",
    )

    storage.upsert_many([tender], notification_candidates=[tender])
    storage.upsert_many([tender], notification_candidates=[tender])

    assert [item.unique_key for item in storage.fetch_pending_notifications()] == [
        tender.unique_key
    ]


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
