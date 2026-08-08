from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

from tender_parser.models import TenderRecord

SQLITE_TIMEOUT_SECONDS = 30.0


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _str_to_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


ACTIONABLE_PRIORITIES = {"hot", "review", "wide"}


class TenderStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        # timeout против «database is locked» при параллельном watcher'е;
        # соединения закрываются явно (closing) — иначе Windows держит файл.
        conn = sqlite3.connect(self.db_path, timeout=SQLITE_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
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
                    match_confidence TEXT,
                    review_priority TEXT,
                    detail_status TEXT,
                    document_matches TEXT,
                    delivery_region_evidence TEXT,
                    source_confidence REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    unique_key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    FOREIGN KEY(unique_key) REFERENCES tenders(unique_key)
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tenders)").fetchall()
            }
            if "match_confidence" not in columns:
                conn.execute("ALTER TABLE tenders ADD COLUMN match_confidence TEXT")
            if "review_priority" not in columns:
                conn.execute("ALTER TABLE tenders ADD COLUMN review_priority TEXT")
            if "detail_status" not in columns:
                conn.execute("ALTER TABLE tenders ADD COLUMN detail_status TEXT")
            if "document_matches" not in columns:
                conn.execute("ALTER TABLE tenders ADD COLUMN document_matches TEXT")
            if "delivery_region_evidence" not in columns:
                conn.execute("ALTER TABLE tenders ADD COLUMN delivery_region_evidence TEXT")
            if "source_confidence" not in columns:
                conn.execute("ALTER TABLE tenders ADD COLUMN source_confidence REAL")

    def upsert_many(
        self,
        tenders: list[TenderRecord],
        *,
        notification_candidates: list[TenderRecord] | None = None,
    ) -> list[TenderRecord]:
        """Сохраняет карточки и возвращает новые для CRM-очереди.

        Новыми считаются впервые увиденные карточки и карточки, впервые
        перешедшие из неактионабельного статуса в hot/review/wide.
        """
        now = datetime.now().isoformat(timespec="seconds")
        first_seen: list[TenderRecord] = []
        with closing(self._connect()) as conn, conn:
            for tender in tenders:
                discovered = _dt_to_str(tender.discovered_at) or now
                if self._is_new(conn, tender):
                    first_seen.append(tender)
                conn.execute(
                    """
                    INSERT INTO tenders (
                        unique_key, title, url, source, tender_number, customer, region,
                        price, deadline, status, published_at, discovered_at, last_seen_at,
                        raw_text, category, include_reason, exclude_reason, filter_status,
                        match_confidence, review_priority, detail_status, document_matches,
                        delivery_region_evidence, source_confidence
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        category=excluded.category,
                        include_reason=excluded.include_reason,
                        exclude_reason=excluded.exclude_reason,
                        filter_status=excluded.filter_status,
                        match_confidence=excluded.match_confidence,
                        review_priority=excluded.review_priority,
                        detail_status=CASE
                            WHEN excluded.detail_status='not_checked' THEN detail_status
                            ELSE excluded.detail_status
                        END,
                        document_matches=CASE
                            WHEN excluded.document_matches='[]' THEN document_matches
                            ELSE excluded.document_matches
                        END,
                        delivery_region_evidence=COALESCE(
                            NULLIF(excluded.delivery_region_evidence, ''), delivery_region_evidence
                        ),
                        source_confidence=MAX(source_confidence, excluded.source_confidence)
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
                        tender.review_priority,
                        tender.detail_status,
                        json.dumps(tender.document_matches, ensure_ascii=False),
                        tender.delivery_region_evidence,
                        tender.source_confidence,
                    ),
                )
            for candidate in notification_candidates or []:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_outbox (unique_key, created_at)
                    VALUES (?, ?)
                    """,
                    (candidate.unique_key, now),
                )
        return first_seen

    def preview_new(self, tenders: list[TenderRecord]) -> list[TenderRecord]:
        """Как upsert_many, но только чтение: ничего не фиксирует в БД."""
        with closing(self._connect()) as conn:
            return [tender for tender in tenders if self._is_new(conn, tender)]

    def merge_with_history(self, tenders: list[TenderRecord]) -> list[TenderRecord]:
        """Возвращает карточки, дополненные уже известными непустыми полями.

        Площадки иногда отдают неполную карточку при повторном запуске. Без этого
        шага текущий отчёт теряет цену, срок или регион, хотя SQLite их помнит.
        Свежие непустые значения всегда имеют приоритет над историческими.
        """
        if not tenders:
            return []

        historical: dict[str, TenderRecord] = {}
        keys = list(dict.fromkeys(tender.unique_key for tender in tenders))
        with closing(self._connect()) as conn:
            for start in range(0, len(keys), 500):
                chunk = keys[start : start + 500]
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT * FROM tenders WHERE unique_key IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    historical[str(row["unique_key"])] = self._row_to_record(row)

        return [self._merge_record(tender, historical.get(tender.unique_key)) for tender in tenders]

    @staticmethod
    def _merge_record(current: TenderRecord, previous: TenderRecord | None) -> TenderRecord:
        if previous is None:
            return current
        detail_status = current.detail_status
        if detail_status == "not_checked" and previous.detail_status != "not_checked":
            detail_status = previous.detail_status
        return replace(
            current,
            customer=current.customer or previous.customer,
            region=current.region or previous.region,
            price=current.price if current.price is not None else previous.price,
            deadline=current.deadline or previous.deadline,
            status=current.status or previous.status,
            published_at=current.published_at or previous.published_at,
            raw_text=current.raw_text or previous.raw_text,
            detail_status=detail_status,
            document_matches=current.document_matches or previous.document_matches,
            delivery_region_evidence=(
                current.delivery_region_evidence or previous.delivery_region_evidence
            ),
            source_confidence=max(current.source_confidence, previous.source_confidence),
        )

    def _is_new(self, conn: sqlite3.Connection, tender: TenderRecord) -> bool:
        exists = conn.execute(
            "SELECT review_priority FROM tenders WHERE unique_key = ?",
            (tender.unique_key,),
        ).fetchone()
        if exists is None:
            return True
        return (
            exists["review_priority"] not in ACTIONABLE_PRIORITIES
            and tender.review_priority in ACTIONABLE_PRIORITIES
        )

    def fetch_by_status(self, status: str) -> list[TenderRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM tenders WHERE filter_status = ? ORDER BY deadline ASC, title ASC",
                (status,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def fetch_pending_notifications(self, limit: int = 50) -> list[TenderRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT tenders.*
                FROM notification_outbox
                JOIN tenders USING (unique_key)
                WHERE notification_outbox.sent_at IS NULL
                ORDER BY notification_outbox.created_at ASC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def mark_notifications_sent(self, unique_keys: list[str]) -> None:
        if not unique_keys:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                """
                UPDATE notification_outbox
                SET sent_at = ?, attempt_count = attempt_count + 1, last_error = NULL
                WHERE unique_key = ? AND sent_at IS NULL
                """,
                [(now, key) for key in unique_keys],
            )

    def mark_notification_error(self, unique_keys: list[str], error: str) -> None:
        if not unique_keys:
            return
        safe_error = error[:500]
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                """
                UPDATE notification_outbox
                SET attempt_count = attempt_count + 1, last_error = ?
                WHERE unique_key = ? AND sent_at IS NULL
                """,
                [(safe_error, key) for key in unique_keys],
            )

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
            review_priority=row["review_priority"],
            detail_status=row["detail_status"] or "not_checked",
            document_matches=json.loads(row["document_matches"] or "[]"),
            delivery_region_evidence=row["delivery_region_evidence"] or "",
            source_confidence=row["source_confidence"] or 0.0,
        )
