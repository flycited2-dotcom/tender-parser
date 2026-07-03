from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from tender_parser.models import TenderRecord
from tender_parser.storage import _dt_to_str, _str_to_dt


class RtsAccumulator:
    """Накопитель сырых строк RTS-кабинета между запусками.

    Пользователь листает выдачу вручную и добавляет каждую видимую страницу;
    накопленное потом прогоняется через общий конвейер профилем rts-accumulated.
    """

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
                CREATE TABLE IF NOT EXISTS rts_cabinet_raw (
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
                    first_added_at TEXT,
                    last_seen_at TEXT,
                    raw_text TEXT,
                    detail_status TEXT,
                    source_confidence REAL
                )
                """
            )

    def add_many(self, tenders: list[TenderRecord]) -> tuple[int, int]:
        now = datetime.now().isoformat(timespec="seconds")
        added = 0
        with self._connect() as conn:
            for tender in tenders:
                exists = conn.execute(
                    "SELECT 1 FROM rts_cabinet_raw WHERE unique_key = ?",
                    (tender.unique_key,),
                ).fetchone()
                if exists is None:
                    added += 1
                conn.execute(
                    """
                    INSERT INTO rts_cabinet_raw (
                        unique_key, title, url, source, tender_number, customer, region,
                        price, deadline, status, published_at, first_added_at, last_seen_at,
                        raw_text, detail_status, source_confidence
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(unique_key) DO UPDATE SET
                        title=excluded.title,
                        url=excluded.url,
                        customer=COALESCE(NULLIF(excluded.customer, ''), customer),
                        region=COALESCE(NULLIF(excluded.region, ''), region),
                        price=COALESCE(excluded.price, price),
                        deadline=COALESCE(NULLIF(excluded.deadline, ''), deadline),
                        status=COALESCE(NULLIF(excluded.status, ''), status),
                        published_at=COALESCE(NULLIF(excluded.published_at, ''), published_at),
                        last_seen_at=excluded.last_seen_at,
                        raw_text=COALESCE(NULLIF(excluded.raw_text, ''), raw_text),
                        detail_status=excluded.detail_status,
                        source_confidence=excluded.source_confidence
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
                        _dt_to_str(tender.discovered_at) or now,
                        now,
                        tender.raw_text,
                        tender.detail_status,
                        tender.source_confidence,
                    ),
                )
            total = int(conn.execute("SELECT COUNT(*) FROM rts_cabinet_raw").fetchone()[0])
        return added, total

    def load_all(self) -> list[TenderRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rts_cabinet_raw ORDER BY deadline ASC, title ASC"
            ).fetchall()
        return [_row_to_record(row) for row in rows]


class RtsAccumulatorSource:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return RtsAccumulator(self.db_path).load_all()


def _row_to_record(row: sqlite3.Row) -> TenderRecord:
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
        discovered_at=_str_to_dt(row["first_added_at"]),
        raw_text=row["raw_text"] or "",
        detail_status=row["detail_status"] or "not_checked",
        source_confidence=row["source_confidence"] or 0.0,
    )
