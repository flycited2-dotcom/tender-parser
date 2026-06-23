from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from tender_parser.models import TenderRecord


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _str_to_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class TenderStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tenders (
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
                    match_confidence TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tenders)").fetchall()
            }
            if "match_confidence" not in columns:
                conn.execute("ALTER TABLE tenders ADD COLUMN match_confidence TEXT")

    def upsert_many(self, tenders: list[TenderRecord]) -> list[TenderRecord]:
        now = datetime.now().isoformat(timespec="seconds")
        first_seen: list[TenderRecord] = []
        with self._connect() as conn:
            for tender in tenders:
                discovered = _dt_to_str(tender.discovered_at) or now
                exists = conn.execute(
                    "SELECT 1 FROM tenders WHERE unique_key = ?",
                    (tender.unique_key,),
                ).fetchone()
                if exists is None:
                    first_seen.append(tender)
                conn.execute(
                    """
                    INSERT INTO tenders (
                        unique_key, title, url, source, tender_number, customer, region,
                        price, deadline, status, published_at, discovered_at, last_seen_at,
                        raw_text, category, include_reason, exclude_reason, filter_status,
                        match_confidence
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(unique_key) DO UPDATE SET
                        title=excluded.title,
                        url=excluded.url,
                        customer=excluded.customer,
                        region=excluded.region,
                        price=excluded.price,
                        deadline=excluded.deadline,
                        status=excluded.status,
                        published_at=excluded.published_at,
                        last_seen_at=excluded.last_seen_at,
                        raw_text=excluded.raw_text,
                        category=excluded.category,
                        include_reason=excluded.include_reason,
                        exclude_reason=excluded.exclude_reason,
                        filter_status=excluded.filter_status,
                        match_confidence=excluded.match_confidence
                    """,
                    (
                        tender.unique_key,
                        tender.title,
                        tender.url,
                        tender.source,
                        tender.tender_number,
                        tender.customer,
                        tender.region,
                        tender.price,
                        _dt_to_str(tender.deadline),
                        tender.status,
                        _dt_to_str(tender.published_at),
                        discovered,
                        now,
                        tender.raw_text,
                        tender.category,
                        tender.include_reason,
                        tender.exclude_reason,
                        tender.filter_status,
                        tender.match_confidence,
                    ),
                )
        return first_seen

    def fetch_by_status(self, status: str) -> list[TenderRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tenders WHERE filter_status = ? ORDER BY deadline ASC, title ASC",
                (status,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> TenderRecord:
        return TenderRecord(
            title=row["title"],
            url=row["url"],
            source=row["source"],
            tender_number=row["tender_number"],
            customer=row["customer"],
            region=row["region"],
            price=row["price"],
            deadline=_str_to_dt(row["deadline"]),
            status=row["status"],
            published_at=_str_to_dt(row["published_at"]),
            discovered_at=_str_to_dt(row["discovered_at"]),
            raw_text=row["raw_text"] or "",
            category=row["category"],
            include_reason=row["include_reason"] or "",
            exclude_reason=row["exclude_reason"] or "",
            filter_status=row["filter_status"],
            match_confidence=row["match_confidence"],
        )
