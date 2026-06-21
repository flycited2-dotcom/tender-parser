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
