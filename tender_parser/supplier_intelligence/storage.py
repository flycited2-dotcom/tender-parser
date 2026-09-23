"""Canonical, restart-safe state for Supplier Intelligence.

Google Sheets is a projection of this database.  Every fact keeps its Gmail
source; prices are append-only and message processing is idempotent per mailbox.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .resolver import (
    OWN_EMAILS,
    identity_from_message,
    is_public_domain,
    match_candidates,
    normalize_company,
    normalize_domain,
    normalize_email,
    normalize_inn,
    normalize_phone,
)


PARSER_VERSION = "supplier-intelligence-1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _value(mapping: dict, *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _confidence(item: dict, default: float = 0.7) -> float:
    raw = item.get("confidence")
    return default if raw is None or raw == "" else float(raw)


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS processed_messages (
  gmail_account TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '',
  history_id TEXT NOT NULL DEFAULT '', processed_at TEXT NOT NULL,
  parser_version TEXT NOT NULL, status TEXT NOT NULL,
  PRIMARY KEY (gmail_account, message_id)
);
CREATE INDEX IF NOT EXISTS idx_processed_thread ON processed_messages(gmail_account, thread_id);
CREATE TABLE IF NOT EXISTS gmail_sync_state (
  gmail_account TEXT PRIMARY KEY, history_id TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gmail_backfill_checkpoint (
  gmail_account TEXT PRIMARY KEY, page_token TEXT, start_history_id TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id TEXT UNIQUE,
  company_name TEXT NOT NULL DEFAULT '', legal_name TEXT NOT NULL DEFAULT '',
  short_name TEXT NOT NULL DEFAULT '', inn TEXT NOT NULL DEFAULT '',
  kpp TEXT NOT NULL DEFAULT '', ogrn TEXT NOT NULL DEFAULT '',
  supplier_type TEXT NOT NULL DEFAULT '', manufacturer TEXT NOT NULL DEFAULT '',
  distributor TEXT NOT NULL DEFAULT '', dealer TEXT NOT NULL DEFAULT '',
  entity_role TEXT NOT NULL DEFAULT 'SUPPLIER',
  website TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
  primary_email TEXT NOT NULL DEFAULT '', primary_phone TEXT NOT NULL DEFAULT '',
  first_contact_date TEXT NOT NULL DEFAULT '', last_contact_date TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0, review_status TEXT NOT NULL DEFAULT 'NEW',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suppliers_inn ON suppliers(inn);
CREATE INDEX IF NOT EXISTS idx_suppliers_domain ON suppliers(domain);
CREATE TABLE IF NOT EXISTS aliases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
  alias_type TEXT NOT NULL, alias_value TEXT NOT NULL, normalized_value TEXT NOT NULL,
  confidence REAL NOT NULL, source_mailbox TEXT NOT NULL, source_message_id TEXT NOT NULL,
  source_thread_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  UNIQUE(supplier_id, alias_type, normalized_value)
);
CREATE INDEX IF NOT EXISTS idx_alias_lookup ON aliases(alias_type, normalized_value);
CREATE TABLE IF NOT EXISTS alias_evidence (
  alias_id INTEGER NOT NULL REFERENCES aliases(id), gmail_account TEXT NOT NULL,
  message_id TEXT NOT NULL, thread_id TEXT NOT NULL, seen_at TEXT NOT NULL,
  PRIMARY KEY(alias_id, gmail_account, message_id)
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT UNIQUE,
  supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
  full_name TEXT NOT NULL DEFAULT '', first_name TEXT NOT NULL DEFAULT '',
  last_name TEXT NOT NULL DEFAULT '', patronymic TEXT NOT NULL DEFAULT '',
  position TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '', phone_ext TEXT NOT NULL DEFAULT '',
  telegram TEXT NOT NULL DEFAULT '', whatsapp TEXT NOT NULL DEFAULT '',
  is_primary INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  source_mailbox TEXT NOT NULL, source_message_id TEXT NOT NULL,
  source_thread_id TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.7
);
CREATE INDEX IF NOT EXISTS idx_contacts_supplier ON contacts(supplier_id);
CREATE TABLE IF NOT EXISTS contact_evidence (
  contact_id INTEGER NOT NULL REFERENCES contacts(id), gmail_account TEXT NOT NULL,
  message_id TEXT NOT NULL, thread_id TEXT NOT NULL, seen_at TEXT NOT NULL,
  PRIMARY KEY(contact_id, gmail_account, message_id)
);
CREATE TABLE IF NOT EXISTS supplier_categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
  category_l1 TEXT NOT NULL DEFAULT '', category_l2 TEXT NOT NULL DEFAULT '',
  category_l3 TEXT NOT NULL DEFAULT '', category_l4 TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.7,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  source_mailbox TEXT NOT NULL, source_message_id TEXT NOT NULL, source_thread_id TEXT NOT NULL,
  UNIQUE(supplier_id, category_l1, category_l2, category_l3, category_l4)
);
CREATE TABLE IF NOT EXISTS category_evidence (
  category_id INTEGER NOT NULL REFERENCES supplier_categories(id),
  gmail_account TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
  seen_at TEXT NOT NULL, evidence_type TEXT NOT NULL DEFAULT 'MESSAGE',
  PRIMARY KEY(category_id, gmail_account, message_id)
);
CREATE TABLE IF NOT EXISTS supplier_products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
  brand TEXT NOT NULL DEFAULT '', manufacturer TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '', article TEXT NOT NULL DEFAULT '',
  product_name TEXT NOT NULL DEFAULT '',
  category_l1 TEXT NOT NULL DEFAULT '', category_l2 TEXT NOT NULL DEFAULT '',
  category_l3 TEXT NOT NULL DEFAULT '', category_l4 TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.7,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  rfq_id TEXT NOT NULL DEFAULT '', tender_id TEXT NOT NULL DEFAULT '',
  source_mailbox TEXT NOT NULL, source_message_id TEXT NOT NULL, source_thread_id TEXT NOT NULL,
  UNIQUE(supplier_id, brand, manufacturer, model, article, product_name)
);
CREATE TABLE IF NOT EXISTS product_evidence (
  product_id INTEGER NOT NULL REFERENCES supplier_products(id),
  gmail_account TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
  seen_at TEXT NOT NULL, rfq_id TEXT NOT NULL DEFAULT '', tender_id TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(product_id, gmail_account, message_id)
);
CREATE TABLE IF NOT EXISTS interactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, interaction_id TEXT UNIQUE,
  supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
  gmail_account TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
  tender_id TEXT NOT NULL DEFAULT '', rfq_id TEXT NOT NULL DEFAULT '',
  date TEXT NOT NULL DEFAULT '', direction TEXT NOT NULL DEFAULT '',
  from_email TEXT NOT NULL DEFAULT '', to_email TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '', message_type TEXT NOT NULL DEFAULT '',
  response_type TEXT NOT NULL DEFAULT '', refusal_reason TEXT NOT NULL DEFAULT '',
  has_attachment INTEGER NOT NULL DEFAULT 0,
  short_summary TEXT NOT NULL DEFAULT '', category_context TEXT NOT NULL DEFAULT '[]',
  processed_at TEXT NOT NULL,
  UNIQUE(gmail_account, message_id, supplier_id)
);
CREATE INDEX IF NOT EXISTS idx_interactions_thread ON interactions(gmail_account, thread_id);
CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id TEXT UNIQUE,
  supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
  gmail_account TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
  tender_id TEXT NOT NULL DEFAULT '', rfq_id TEXT NOT NULL DEFAULT '', line_index INTEGER NOT NULL,
  quote_number TEXT NOT NULL DEFAULT '', quote_date TEXT NOT NULL DEFAULT '',
  quote_valid_until TEXT NOT NULL DEFAULT '', product_name TEXT NOT NULL DEFAULT '',
  brand TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', article TEXT NOT NULL DEFAULT '',
  quantity TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '',
  price_unit TEXT NOT NULL DEFAULT '', price_total TEXT NOT NULL DEFAULT '',
  currency TEXT NOT NULL DEFAULT '', vat_rate TEXT NOT NULL DEFAULT '',
  vat_included TEXT NOT NULL DEFAULT '', without_vat TEXT NOT NULL DEFAULT '',
  discount_percent TEXT NOT NULL DEFAULT '', discount_amount TEXT NOT NULL DEFAULT '',
  availability TEXT NOT NULL DEFAULT '', production_days TEXT NOT NULL DEFAULT '',
  delivery_days TEXT NOT NULL DEFAULT '', delivery_city TEXT NOT NULL DEFAULT '',
  delivery_cost TEXT NOT NULL DEFAULT '', delivery_included TEXT NOT NULL DEFAULT '',
  payment_terms TEXT NOT NULL DEFAULT '', prepayment_percent TEXT NOT NULL DEFAULT '',
  warranty TEXT NOT NULL DEFAULT '', country_origin TEXT NOT NULL DEFAULT '',
  manufacturer TEXT NOT NULL DEFAULT '', attachment_filename TEXT NOT NULL DEFAULT '',
  attachment_type TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  UNIQUE(gmail_account, message_id, line_index)
);
CREATE INDEX IF NOT EXISTS idx_quotes_supplier ON quotes(supplier_id);
CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id TEXT REFERENCES suppliers(supplier_id),
  gmail_account TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
  attachment_id TEXT NOT NULL, filename TEXT NOT NULL DEFAULT '',
  mime_type TEXT NOT NULL DEFAULT '', file_size INTEGER NOT NULL DEFAULT 0,
  extracted_text_status TEXT NOT NULL DEFAULT '', parse_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(gmail_account, message_id, attachment_id)
);
CREATE TABLE IF NOT EXISTS review_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT UNIQUE,
  type TEXT NOT NULL, supplier_candidate_1 TEXT NOT NULL DEFAULT '',
  supplier_candidate_2 TEXT NOT NULL DEFAULT '', value TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '', gmail_account TEXT NOT NULL DEFAULT '',
  message_id TEXT NOT NULL DEFAULT '', thread_id TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'NEW',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT, gmail_account TEXT NOT NULL DEFAULT '',
  message_id TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL, error_text TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
  UNIQUE(gmail_account, message_id, stage)
);
CREATE TABLE IF NOT EXISTS processing_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE,
  started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
  mailbox TEXT NOT NULL, messages_found INTEGER NOT NULL DEFAULT 0,
  messages_processed INTEGER NOT NULL DEFAULT 0,
  suppliers_created INTEGER NOT NULL DEFAULT 0, suppliers_updated INTEGER NOT NULL DEFAULT 0,
  contacts_created INTEGER NOT NULL DEFAULT 0, quotes_created INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, error_text TEXT NOT NULL DEFAULT ''
);
"""


class SupplierStore:
    def __init__(self, path: str | Path, confidence_threshold: float = 0.70) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.confidence_threshold = confidence_threshold
        with self._connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def seen(self, mailbox: str, message_id: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM processed_messages WHERE gmail_account=? AND message_id=?",
                (mailbox.casefold().strip(), str(message_id).strip()),
            ).fetchone() is not None

    def get_sync_state(self, mailbox: str) -> str:
        with self._connect() as db:
            row = db.execute(
                "SELECT history_id FROM gmail_sync_state WHERE gmail_account=?",
                (mailbox.casefold().strip(),),
            ).fetchone()
            return str(row[0]) if row else ""

    def set_sync_state(self, mailbox: str, history_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO gmail_sync_state(gmail_account,history_id,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(gmail_account) DO UPDATE SET history_id=excluded.history_id,updated_at=excluded.updated_at",
                (mailbox.casefold().strip(), str(history_id).strip(), _now()),
            )

    def get_checkpoint(self, mailbox: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT page_token FROM gmail_backfill_checkpoint WHERE gmail_account=?",
                (mailbox.casefold().strip(),),
            ).fetchone()
            return str(row[0]) if row and row[0] is not None else None

    def set_checkpoint(self, mailbox: str, page_token: str | None) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO gmail_backfill_checkpoint(gmail_account,page_token,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(gmail_account) DO UPDATE SET page_token=excluded.page_token,updated_at=excluded.updated_at",
                (mailbox.casefold().strip(), page_token, _now()),
            )

    def get_backfill_start_history(self, mailbox: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT start_history_id FROM gmail_backfill_checkpoint WHERE gmail_account=?",
                (mailbox.casefold().strip(),),
            ).fetchone()
            return str(row[0]) if row and row[0] is not None else None

    def set_backfill_start_history(self, mailbox: str, history_id: str | None) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO gmail_backfill_checkpoint(gmail_account,start_history_id,updated_at) "
                "VALUES(?,?,?) ON CONFLICT(gmail_account) DO UPDATE SET "
                "start_history_id=excluded.start_history_id,updated_at=excluded.updated_at",
                (mailbox.casefold().strip(), history_id, _now()),
            )

    def record_error(self, mailbox: str, message_id: str, exc_text: str) -> None:
        """Persist a per-message failure without changing its processed state."""
        with self._connect() as db:
            db.execute(
                "INSERT INTO errors(gmail_account,message_id,stage,error_text,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(gmail_account,message_id,stage) DO UPDATE SET "
                "error_text=excluded.error_text,created_at=excluded.created_at,"
                "attempt_count=errors.attempt_count+1",
                (mailbox.casefold().strip(), str(message_id), "collector", str(exc_text)[:2000], _now()),
            )

    def pending_errors(self, mailbox: str) -> list[str]:
        with self._connect() as db:
            return [row[0] for row in db.execute(
                "SELECT DISTINCT message_id FROM errors WHERE gmail_account=? AND message_id<>'' "
                "AND NOT EXISTS(SELECT 1 FROM processed_messages p WHERE p.gmail_account=errors.gmail_account "
                "AND p.message_id=errors.message_id) ORDER BY message_id",
                (mailbox.casefold().strip(),),
            )]

    def clear_error(self, mailbox: str, message_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM errors WHERE gmail_account=? AND message_id=?",
                       (mailbox.casefold().strip(), str(message_id)))

    def record_run(self, row: dict) -> str:
        """Append an orchestrator-level run summary and return its stable RUN_ID."""
        row = {"started_at": _now(), "finished_at": _now(), "status": "UNKNOWN", **row}
        fields = (
            "started_at", "finished_at", "mailbox", "messages_found", "messages_processed",
            "suppliers_created", "suppliers_updated", "contacts_created", "quotes_created",
            "error_count", "status", "error_text",
        )
        values = tuple(row.get(name, "") if name not in {
            "messages_found", "messages_processed", "suppliers_created", "suppliers_updated",
            "contacts_created", "quotes_created", "error_count",
        } else int(row.get(name) or 0) for name in fields)
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO processing_log(" + ",".join(fields) + ") VALUES(" +
                ",".join("?" for _ in fields) + ")", values,
            )
            run_id = str(row.get("run_id") or f"RUN-{cursor.lastrowid:08d}")
            db.execute("UPDATE processing_log SET run_id=? WHERE id=?", (run_id, cursor.lastrowid))
            return run_id

    def ingest(self, message: dict, force: bool = False) -> dict[str, Any]:
        """Store one normalized Gmail message atomically and return compact counters."""
        mailbox = normalize_email(message.get("gmail_account"))
        message_id = str(message.get("message_id") or "").strip()
        if not mailbox or not message_id:
            raise ValueError("gmail_account and message_id are required")
        thread_id = str(message.get("thread_id") or "").strip()
        started = _now()
        counts: dict[str, Any] = {
            "status": "processed", "messages_processed": 0, "suppliers_created": 0,
            "suppliers_updated": 0, "contacts_created": 0, "quotes_created": 0,
            "review_items": 0, "conflicts": 0, "attachments_created": 0, "error_count": 0,
            "supplier_id": "",
        }
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                previously_seen = db.execute(
                    "SELECT 1 FROM processed_messages WHERE gmail_account=? AND message_id=?",
                    (mailbox, message_id),
                ).fetchone() is not None
                if previously_seen and not force:
                    counts["status"] = "duplicate"
                    return counts
                if previously_seen:
                    affected = self._forget_message(db, mailbox, message_id)
                    counts["status"] = "reprocessed"
                else:
                    affected = set()
                self._ingest_in_transaction(db, message, mailbox, message_id, thread_id, counts, started)
                counts["conflicts"] = counts["review_items"]
                db.execute(
                    "INSERT INTO processed_messages VALUES(?,?,?,?,?,?,?)",
                    (mailbox, message_id, thread_id, str(message.get("history_id") or ""),
                     _now(), PARSER_VERSION, str(counts["status"])),
                )
                counts["messages_processed"] = 1
                for supplier_id in affected:
                    self._rebuild_supplier(db, supplier_id)
        except Exception as exc:
            counts["status"] = "error"
            counts["error_count"] = 1
            counts["error_text"] = f"{type(exc).__name__}: {exc}"
            with self._connect() as db:
                db.execute(
                    "INSERT INTO errors(gmail_account,message_id,stage,error_text,created_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(gmail_account,message_id,stage) DO UPDATE SET "
                    "error_text=excluded.error_text,created_at=excluded.created_at,"
                    "attempt_count=errors.attempt_count+1",
                    (mailbox, message_id, "ingest", counts["error_text"], _now()),
                )
        return counts

    def _forget_message(self, db: sqlite3.Connection, mailbox: str, message_id: str) -> set[str]:
        affected = {
            row[0] for row in db.execute(
                "SELECT supplier_id FROM interactions WHERE gmail_account=? AND message_id=?",
                (mailbox, message_id),
            )
        }
        for evidence in ("alias_evidence", "contact_evidence", "category_evidence", "product_evidence"):
            db.execute(f"DELETE FROM {evidence} WHERE gmail_account=? AND message_id=?", (mailbox, message_id))
        for table, key, evidence in (
            ("aliases", "alias_id", "alias_evidence"),
            ("contacts", "contact_id", "contact_evidence"),
            ("supplier_categories", "category_id", "category_evidence"),
            ("supplier_products", "product_id", "product_evidence"),
        ):
            db.execute(f"DELETE FROM {table} WHERE NOT EXISTS "
                       f"(SELECT 1 FROM {evidence} WHERE {evidence}.{key}={table}.id)")
            stale = db.execute(
                f"SELECT id FROM {table} WHERE source_mailbox=? AND source_message_id=?",
                (mailbox, message_id),
            ).fetchall()
            for row in stale:
                first = db.execute(
                    f"SELECT gmail_account,message_id,thread_id,seen_at FROM {evidence} "
                    f"WHERE {key}=? ORDER BY seen_at,gmail_account,message_id LIMIT 1",
                    (row["id"],),
                ).fetchone()
                last = db.execute(
                    f"SELECT MAX(seen_at) FROM {evidence} WHERE {key}=?", (row["id"],),
                ).fetchone()[0]
                if first:
                    db.execute(
                        f"UPDATE {table} SET source_mailbox=?,source_message_id=?,source_thread_id=?,"
                        "first_seen_at=?,last_seen_at=? WHERE id=?",
                        (first["gmail_account"], first["message_id"], first["thread_id"],
                         first["seen_at"], last, row["id"]),
                    )
        for table in ("quotes", "interactions", "attachments", "review_queue", "processed_messages"):
            db.execute(f"DELETE FROM {table} WHERE gmail_account=? AND message_id=?", (mailbox, message_id))
        return affected

    def _ingest_in_transaction(
        self, db: sqlite3.Connection, message: dict, mailbox: str, message_id: str,
        thread_id: str, counts: dict[str, Any], started: str,
    ) -> None:
        direction = str(message.get("direction") or "").upper()
        own_emails = OWN_EMAILS | {mailbox} | {
            normalize_email(value) for value in (message.get("own_emails") or [])
        }
        recipients = list(dict.fromkeys(
            email for email in (
                normalize_email(value)
                for value in [*(message.get("to_emails") or []), *(message.get("cc_emails") or [])]
            )
            if email and email not in own_emails and normalize_domain(email) != "simfer.com.ru"
        )) if direction == "OUT" else []
        if len(recipients) < 2:
            self._ingest_supplier_fact(db, message, mailbox, message_id, thread_id, counts, started)
            return

        original_supplier = message.get("supplier") if isinstance(message.get("supplier"), dict) else {}
        original_email = normalize_email(original_supplier.get("email"))
        primary = original_email if original_email in recipients else recipients[0]
        for email in recipients:
            per_recipient = dict(message)
            per_recipient["supplier"] = (
                {**original_supplier, "email": email} if email == primary
                else {"email": email, "domain": normalize_domain(email)}
            )
            per_recipient["signature"] = {}
            per_recipient["to_emails"] = [email]
            per_recipient["cc_emails"] = []
            per_recipient["_multi_recipient"] = True
            per_recipient["quote_lines"] = []
            self._ingest_supplier_fact(
                db, per_recipient, mailbox, message_id, thread_id, counts, started,
                allow_thread_inherit=False,
            )
        if message.get("quote_lines"):
            self._review(db, "QUOTE_PARSE_ERROR", "", "", str(message.get("subject") or ""),
                         "Исходящее письмо нескольким поставщикам содержит цену; принадлежность КП неясна",
                         mailbox, message_id, thread_id, 0.0)
            counts["review_items"] += 1

    def _ingest_supplier_fact(
        self, db: sqlite3.Connection, message: dict, mailbox: str, message_id: str,
        thread_id: str, counts: dict[str, Any], started: str, *,
        allow_thread_inherit: bool = True,
    ) -> None:
        date = str(message.get("date") or started)
        direction = str(message.get("direction") or "").upper()
        supplier = message.get("supplier") if isinstance(message.get("supplier"), dict) else {}
        signature = message.get("signature") if isinstance(message.get("signature"), dict) else {}
        categories = [x for x in (message.get("categories") or []) if isinstance(x, dict)]
        products = [x for x in (message.get("products") or []) if isinstance(x, dict)]
        quotes = [x for x in (message.get("quote_lines") or []) if isinstance(x, dict)]
        attachments = [x for x in (message.get("attachments") or []) if isinstance(x, dict)]
        supplier_signal = message.get("supplier_signal")
        if supplier_signal is None:
            supplier_signal = bool(categories or products or quotes or supplier or signature)
        thread_rows = db.execute(
            "SELECT supplier_id,tender_id,rfq_id,category_context FROM interactions "
            "WHERE gmail_account=? AND thread_id=? ORDER BY id DESC",
            (mailbox, thread_id),
        ).fetchall() if thread_id else []
        prior = thread_rows[0] if thread_rows else None
        identity = identity_from_message(message)
        if not supplier_signal and not prior:
            counts["status"] = "ignored"
            return
        if identity.email in OWN_EMAILS and not identity.company:
            counts["status"] = "ignored"
            return
        candidates = match_candidates(
            identity,
            _rows(db.execute("SELECT * FROM suppliers WHERE supplier_id IS NOT NULL")),
            _rows(db.execute("SELECT * FROM aliases")),
        )
        selected = candidates[0] if candidates else None
        thread_suppliers = {str(row["supplier_id"]) for row in thread_rows}
        inherited_id = (
            str(prior["supplier_id"])
            if prior and len(thread_suppliers) == 1 and allow_thread_inherit else ""
        )
        supplier_id = ""
        confidence = selected.confidence if selected else 0.0
        if selected and inherited_id and selected.supplier_id != inherited_id:
            self._review(db, "COMPANY_CONFLICT", inherited_id, selected.supplier_id,
                         identity.email or identity.company, "Поставщик в письме отличается от цепочки",
                         mailbox, message_id, thread_id, selected.confidence)
            counts["review_items"] += 1
            counts["status"] = "review"
        elif selected and self._identity_conflicts(db, selected.supplier_id, identity.inn):
            self._review(db, "COMPANY_CONFLICT", selected.supplier_id, "", identity.inn,
                         "ИНН отличается от существующего поставщика", mailbox, message_id,
                         thread_id, selected.confidence)
            counts["review_items"] += 1
            counts["status"] = "review"
        elif len(candidates) > 1 and selected and candidates[1].confidence >= self.confidence_threshold and selected.confidence - candidates[1].confidence < 0.05:
            self._review(db, "POSSIBLE_DUPLICATE", selected.supplier_id, candidates[1].supplier_id,
                         identity.company or identity.email, "Несколько одинаково вероятных поставщиков",
                         mailbox, message_id, thread_id, selected.confidence)
            counts["review_items"] += 1
            counts["status"] = "review"
        elif selected and confidence >= self.confidence_threshold:
            supplier_id = selected.supplier_id
        elif inherited_id and not selected:
            old = db.execute("SELECT inn,company_name FROM suppliers WHERE supplier_id=?", (inherited_id,)).fetchone()
            mismatch = bool(old and (
                (identity.inn and old["inn"] and identity.inn != old["inn"])
                or (identity.company and normalize_company(old["company_name"])
                    and identity.company != normalize_company(old["company_name"]))
            ))
            if mismatch:
                self._review(db, "COMPANY_CONFLICT", inherited_id, "",
                             identity.company or identity.inn, "Новые реквизиты противоречат цепочке",
                             mailbox, message_id, thread_id, 0.5)
                counts["review_items"] += 1
                counts["status"] = "review"
            else:
                supplier_id = inherited_id
                confidence = 0.9
        elif selected:
            self._review(db, "POSSIBLE_DUPLICATE", selected.supplier_id, "",
                         identity.company or identity.email, "Совпадение ниже порога",
                         mailbox, message_id, thread_id, confidence)
            counts["review_items"] += 1
            counts["status"] = "review"
        elif direction == "IN" and len(thread_suppliers) > 1 and not identity.inn and not identity.company:
            self._review(db, "UNKNOWN_SUPPLIER", "", "", identity.email,
                         "В цепочке несколько поставщиков; новый адрес не удалось связать с одним из них",
                         mailbox, message_id, thread_id, 0.0)
            counts["review_items"] += 1
            counts["status"] = "review"
        elif supplier_signal and any((identity.inn, identity.domain, identity.email, identity.company, identity.website_domain, identity.phone)):
            supplier_id = self._create_supplier(db, supplier, signature, identity, date, started)
            confidence = 1.0 if identity.inn else 0.8 if identity.domain else 0.7
            counts["suppliers_created"] += 1
        elif supplier_signal:
            self._review(db, "UNKNOWN_SUPPLIER", "", "", str(message.get("subject") or ""),
                         "Не удалось определить компанию или адрес поставщика",
                         mailbox, message_id, thread_id, 0.0)
            counts["review_items"] += 1
            counts["status"] = "review"
        else:
            counts["status"] = "ignored"

        tender_id = str(message.get("tender_id") or (prior["tender_id"] if prior else "") or "")
        rfq_id = str(message.get("rfq_id") or (prior["rfq_id"] if prior else "") or "")
        if not supplier_id:
            self._save_attachments(db, attachments, "", mailbox, message_id, thread_id, started, counts)
            return
        if not counts["supplier_id"]:
            counts["supplier_id"] = supplier_id
        counts.setdefault("supplier_ids", [])
        if supplier_id not in counts["supplier_ids"]:
            counts["supplier_ids"].append(supplier_id)
        self._update_supplier(db, supplier_id, supplier, signature, identity, date, started,
                              confidence, mailbox, message_id, thread_id, counts,
                              created_here=bool(selected is None and supplier_id not in thread_suppliers))
        self._upsert_contact(db, supplier_id, supplier, signature, identity, date,
                             mailbox, message_id, thread_id, confidence, counts, direction)
        category_context = self._save_categories(db, categories, supplier_id, date, mailbox,
                                                  message_id, thread_id, counts)
        if not category_context and prior:
            category_context = json.loads(prior["category_context"] or "[]")
        self._save_products(db, products, supplier_id, date, mailbox, message_id,
                            thread_id, tender_id, rfq_id, counts)
        self._save_interaction(db, message, supplier_id, mailbox, message_id, thread_id,
                               tender_id, rfq_id, date, category_context, started, attachments)
        self._save_quotes(db, quotes, supplier_id, mailbox, message_id, thread_id,
                          tender_id, rfq_id, started, counts)
        self._save_attachments(db, attachments, "" if message.get("_multi_recipient") else supplier_id,
                               mailbox, message_id,
                               thread_id, started, counts)

    def _identity_conflicts(self, db: sqlite3.Connection, supplier_id: str, inn: str) -> bool:
        if not inn:
            return False
        row = db.execute("SELECT inn FROM suppliers WHERE supplier_id=?", (supplier_id,)).fetchone()
        return bool(row and row["inn"] and row["inn"] != inn)

    def _create_supplier(self, db: sqlite3.Connection, supplier: dict, signature: dict,
                         identity: Any, date: str, now: str) -> str:
        name = _value(supplier, "company_name", "name", "legal_name") or _value(signature, "company")
        if not name:
            name = identity.domain or identity.website_domain or identity.email or "Неизвестный поставщик"
        cursor = db.execute(
            "INSERT INTO suppliers(company_name,legal_name,short_name,inn,kpp,ogrn,supplier_type,"
            "manufacturer,distributor,dealer,entity_role,website,domain,city,region,address,primary_email,"
            "primary_phone,first_contact_date,last_contact_date,confidence,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, _value(supplier, "legal_name"), _value(supplier, "short_name"), identity.inn,
             _value(supplier, "kpp"), _value(supplier, "ogrn"), _value(supplier, "supplier_type"),
             _value(supplier, "manufacturer"), _value(supplier, "distributor"), _value(supplier, "dealer"),
             "SUPPLIER",
             _value(supplier, "website") or _value(signature, "website"), identity.domain,
             _value(supplier, "city") or _value(signature, "city"), _value(supplier, "region"),
             _value(supplier, "address"), identity.email, identity.phone,
             date, date, 1.0 if identity.inn else 0.8 if identity.domain else 0.7, now, now),
        )
        supplier_id = f"SUP-{cursor.lastrowid:06d}"
        db.execute("UPDATE suppliers SET supplier_id=? WHERE id=?", (supplier_id, cursor.lastrowid))
        return supplier_id

    def _update_supplier(self, db: sqlite3.Connection, supplier_id: str, supplier: dict,
                         signature: dict, identity: Any, date: str, now: str, confidence: float,
                         mailbox: str, message_id: str, thread_id: str, counts: dict, *,
                         created_here: bool) -> None:
        current = dict(db.execute("SELECT * FROM suppliers WHERE supplier_id=?", (supplier_id,)).fetchone())
        input_values = {
            "company_name": _value(supplier, "company_name", "name") or _value(signature, "company"),
            "legal_name": _value(supplier, "legal_name"), "short_name": _value(supplier, "short_name"),
            "inn": identity.inn, "kpp": _value(supplier, "kpp"), "ogrn": _value(supplier, "ogrn"),
            "supplier_type": _value(supplier, "supplier_type"),
            "manufacturer": _value(supplier, "manufacturer"),
            "distributor": _value(supplier, "distributor"), "dealer": _value(supplier, "dealer"),
            "entity_role": "SUPPLIER",
            "website": _value(supplier, "website") or _value(signature, "website"),
            "domain": identity.domain, "city": _value(supplier, "city") or _value(signature, "city"),
            "region": _value(supplier, "region"), "address": _value(supplier, "address"),
            "primary_email": identity.email, "primary_phone": identity.phone,
        }
        conflicting_aliases: set[tuple[str, str]] = set()
        for key, alias_type, normalizer in (
            ("domain", "DOMAIN", normalize_domain),
            ("website", "WEBSITE", normalize_domain),
            ("primary_email", "EMAIL", normalize_email),
            ("primary_phone", "PHONE", normalize_phone),
        ):
            normalized = normalizer(input_values[key])
            if not normalized:
                continue
            other = db.execute(
                "SELECT supplier_id FROM aliases WHERE alias_type=? AND normalized_value=? "
                "AND supplier_id<>? LIMIT 1", (alias_type, normalized, supplier_id),
            ).fetchone()
            if other:
                self._review(db, "COMPANY_CONFLICT", supplier_id, other["supplier_id"],
                             input_values[key], f"Алиас {alias_type} уже связан с другой компанией",
                             mailbox, message_id, thread_id, confidence)
                counts["review_items"] += 1
                conflicting_aliases.add((alias_type, normalized))
                input_values[key] = ""
        changed = False
        for key, value in input_values.items():
            if not value:
                continue
            if not current[key]:
                current[key] = value
                changed = True
            elif current[key] != value and key in {"inn", "kpp", "ogrn"}:
                self._review(db, "COMPANY_CONFLICT", supplier_id, "", value,
                             f"Расхождение поля {key}: {current[key]}", mailbox,
                             message_id, thread_id, confidence)
                counts["review_items"] += 1
        if not current["first_contact_date"] or date < current["first_contact_date"]:
            current["first_contact_date"] = date
        if not current["last_contact_date"] or date > current["last_contact_date"]:
            current["last_contact_date"] = date
        current["confidence"] = max(float(current["confidence"]), confidence)
        current["updated_at"] = now
        columns = list(input_values) + ["first_contact_date", "last_contact_date", "confidence", "updated_at"]
        db.execute(
            f"UPDATE suppliers SET {','.join(name + '=?' for name in columns)} WHERE supplier_id=?",
            (*(current[name] for name in columns), supplier_id),
        )
        if not created_here:
            counts["suppliers_updated"] += 1
        aliases = [
            ("INN", identity.inn, normalize_inn), ("DOMAIN", identity.domain, normalize_domain),
            ("WEBSITE", input_values["website"], normalize_domain),
            ("EMAIL", identity.email, normalize_email), ("PHONE", identity.phone, normalize_phone),
            ("COMPANY_NAME", input_values["company_name"], normalize_company),
            ("COMPANY_NAME", input_values["legal_name"], normalize_company),
            ("COMPANY_NAME", input_values["short_name"], normalize_company),
        ]
        for email_value in [*(supplier.get("emails") or []), *(signature.get("emails") or [])]:
            aliases.append(("EMAIL", email_value, normalize_email))
        for phone_value in [*(supplier.get("phones") or []), *(signature.get("phones") or [])]:
            aliases.append(("PHONE", phone_value, normalize_phone))
        for alias_type, value, normalizer in aliases:
            normalized = normalizer(value)
            if not normalized or alias_type == "DOMAIN" and is_public_domain(normalized):
                continue
            if (alias_type, normalized) in conflicting_aliases:
                continue
            db.execute(
                "INSERT INTO aliases(supplier_id,alias_type,alias_value,normalized_value,confidence,"
                "source_mailbox,source_message_id,source_thread_id,first_seen_at,last_seen_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(supplier_id,alias_type,normalized_value) "
                "DO UPDATE SET last_seen_at=excluded.last_seen_at,confidence=MAX(aliases.confidence,excluded.confidence)",
                (supplier_id, alias_type, str(value), normalized, confidence,
                 mailbox, message_id, thread_id, date, date),
            )
            alias_row = db.execute(
                "SELECT id FROM aliases WHERE supplier_id=? AND alias_type=? AND normalized_value=?",
                (supplier_id, alias_type, normalized),
            ).fetchone()
            db.execute(
                "INSERT OR IGNORE INTO alias_evidence VALUES(?,?,?,?,?)",
                (alias_row["id"], mailbox, message_id, thread_id, date),
            )

    def _upsert_contact(self, db: sqlite3.Connection, supplier_id: str, supplier: dict,
                        signature: dict, identity: Any, date: str, mailbox: str,
                        message_id: str, thread_id: str, confidence: float, counts: dict,
                        direction: str) -> None:
        if direction != "IN" and not supplier.get("contact"):
            return
        contact = supplier.get("contact") if isinstance(supplier.get("contact"), dict) else {}
        name = _value(contact, "full_name", "person") or _value(signature, "person", "full_name")
        email = normalize_email(_value(contact, "email") or identity.email)
        phones = signature.get("phones") or []
        phone = normalize_phone(_value(contact, "phone") or (phones[0] if phones else identity.phone))
        if not any((name, email, phone)):
            return
        name_parts = name.split()
        cyrillic_name = len(name_parts) == 3 and all(
            all("А" <= letter.upper() <= "Я" or letter in "-Ёё" for letter in part)
            for part in name_parts
        )
        first_name = _value(contact, "first_name") or (name_parts[1] if cyrillic_name else "")
        last_name = _value(contact, "last_name") or (name_parts[0] if cyrillic_name else "")
        patronymic = _value(contact, "patronymic") or (name_parts[2] if cyrillic_name else "")
        rows = _rows(db.execute("SELECT * FROM contacts WHERE supplier_id=?", (supplier_id,)))
        existing = next(
            (row for row in rows if name and normalize_company(row["full_name"]) == normalize_company(name)), None
        )
        if existing is None and email:
            existing = next((row for row in rows if row["email"] == email and (not row["full_name"] or not name)), None)
        if existing:
            db.execute(
                "UPDATE contacts SET full_name=CASE WHEN full_name='' THEN ? ELSE full_name END,"
                "first_name=CASE WHEN first_name='' THEN ? ELSE first_name END,"
                "last_name=CASE WHEN last_name='' THEN ? ELSE last_name END,"
                "patronymic=CASE WHEN patronymic='' THEN ? ELSE patronymic END,"
                "position=CASE WHEN position='' THEN ? ELSE position END,"
                "email=CASE WHEN email='' THEN ? ELSE email END,"
                "phone=CASE WHEN phone='' THEN ? ELSE phone END,"
                "last_seen_at=MAX(last_seen_at,?), confidence=MAX(confidence,?) WHERE id=?",
                (name, first_name, last_name, patronymic,
                 _value(contact, "position") or _value(signature, "position"),
                 email, phone, date, confidence, existing["id"]),
            )
            db.execute("INSERT OR IGNORE INTO contact_evidence VALUES(?,?,?,?,?)",
                       (existing["id"], mailbox, message_id, thread_id, date))
            return
        cursor = db.execute(
            "INSERT INTO contacts(supplier_id,full_name,first_name,last_name,patronymic,position,"
            "email,phone,phone_ext,telegram,whatsapp,is_primary,first_seen_at,last_seen_at,"
            "source_mailbox,source_message_id,source_thread_id,confidence) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (supplier_id, name, first_name, last_name, patronymic,
             _value(contact, "position") or _value(signature, "position"),
             email, phone, _value(contact, "phone_ext"), _value(contact, "telegram"),
             _value(contact, "whatsapp"), 0 if rows else 1, date, date,
             mailbox, message_id, thread_id, confidence),
        )
        db.execute("UPDATE contacts SET contact_id=? WHERE id=?", (f"CON-{cursor.lastrowid:06d}", cursor.lastrowid))
        db.execute("INSERT OR IGNORE INTO contact_evidence VALUES(?,?,?,?,?)",
                   (cursor.lastrowid, mailbox, message_id, thread_id, date))
        counts["contacts_created"] += 1

    def _save_categories(self, db: sqlite3.Connection, categories: list[dict], supplier_id: str,
                         date: str, mailbox: str, message_id: str, thread_id: str,
                         counts: dict) -> list[dict]:
        context: list[dict] = []
        for item in categories:
            levels = tuple(_value(item, f"category_l{i}", f"CATEGORY_L{i}", f"l{i}") for i in range(1, 5))
            if not any(levels):
                continue
            confidence = _confidence(item)
            if confidence < self.confidence_threshold:
                self._review(db, "UNKNOWN_CATEGORY", supplier_id, "", " / ".join(x for x in levels if x),
                             "Уверенность классификации ниже порога",
                             mailbox, message_id, thread_id, confidence)
                counts["review_items"] += 1
                continue
            context.append(dict(zip(("category_l1", "category_l2", "category_l3", "category_l4"), levels)))
            db.execute(
                "INSERT INTO supplier_categories(supplier_id,category_l1,category_l2,category_l3,category_l4,"
                "confidence,first_seen_at,last_seen_at,source_mailbox,source_message_id,source_thread_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(supplier_id,category_l1,category_l2,category_l3,category_l4) "
                "DO UPDATE SET last_seen_at=MAX(supplier_categories.last_seen_at,excluded.last_seen_at),"
                "confidence=MAX(supplier_categories.confidence,excluded.confidence)",
                (supplier_id, *levels, confidence, date, date, mailbox, message_id, thread_id),
            )
            row = db.execute(
                "SELECT id FROM supplier_categories WHERE supplier_id=? AND category_l1=? AND "
                "category_l2=? AND category_l3=? AND category_l4=?", (supplier_id, *levels),
            ).fetchone()
            db.execute(
                "INSERT OR IGNORE INTO category_evidence VALUES(?,?,?,?,?,?)",
                (row["id"], mailbox, message_id, thread_id, date, "MESSAGE"),
            )
        return context

    def _save_products(self, db: sqlite3.Connection, products: list[dict], supplier_id: str,
                       date: str, mailbox: str, message_id: str, thread_id: str,
                       tender_id: str, rfq_id: str, counts: dict) -> None:
        for item in products:
            brand = _value(item, "brand", "BRAND")
            manufacturer = _value(item, "manufacturer", "MANUFACTURER")
            model = _value(item, "model", "MODEL")
            article = _value(item, "article", "ARTICLE", "sku")
            name = _value(item, "product_name", "PRODUCT_NAME", "name")
            if not any((brand, manufacturer, model, article, name)):
                continue
            levels = tuple(_value(item, f"category_l{i}", f"CATEGORY_L{i}", f"l{i}") for i in range(1, 5))
            confidence = _confidence(item)
            if confidence < self.confidence_threshold:
                self._review(db, "PRODUCT_CONFLICT", supplier_id, "", name or model or article,
                             "Уверенность распознавания товара ниже порога",
                             mailbox, message_id, thread_id, confidence)
                counts["review_items"] += 1
                continue
            known = _rows(db.execute("SELECT id,brand,model,article,product_name FROM supplier_products WHERE supplier_id=?", (supplier_id,)))
            row = next((candidate for candidate in known if (
                article and candidate["article"].casefold() == article.casefold()
                and (not brand or not candidate["brand"] or candidate["brand"].casefold() == brand.casefold())
                or model and candidate["model"].casefold() == model.casefold()
                and (not brand or not candidate["brand"] or candidate["brand"].casefold() == brand.casefold())
                or not article and not model and name and candidate["product_name"].casefold() == name.casefold()
                and (not brand or not candidate["brand"] or candidate["brand"].casefold() == brand.casefold())
            )), None)
            if row:
                db.execute(
                    "UPDATE supplier_products SET last_seen_at=MAX(last_seen_at,?),"
                    "confidence=MAX(confidence,?) WHERE id=?", (date, confidence, row["id"]),
                )
            else:
                cursor = db.execute(
                    "INSERT INTO supplier_products(supplier_id,brand,manufacturer,model,article,product_name,"
                    "category_l1,category_l2,category_l3,category_l4,confidence,first_seen_at,last_seen_at,"
                    "rfq_id,tender_id,source_mailbox,source_message_id,source_thread_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (supplier_id, brand, manufacturer, model, article, name, *levels,
                     confidence, date, date, rfq_id, tender_id, mailbox, message_id, thread_id),
                )
                row = {"id": cursor.lastrowid}
            db.execute(
                "INSERT OR IGNORE INTO product_evidence VALUES(?,?,?,?,?,?,?)",
                (row["id"], mailbox, message_id, thread_id, date, rfq_id, tender_id),
            )

    def _save_interaction(self, db: sqlite3.Connection, message: dict, supplier_id: str,
                          mailbox: str, message_id: str, thread_id: str, tender_id: str,
                          rfq_id: str, date: str, category_context: list[dict], now: str,
                          attachments: list[dict]) -> None:
        from_email = normalize_email(message.get("from_email"))
        to_email = ", ".join(normalize_email(x) for x in message.get("to_emails", []) if normalize_email(x))
        body = str(message.get("new_message_body") or "").strip()
        summary = str(message.get("short_summary") or body[:240]).replace("\n", " ").strip()
        cursor = db.execute(
            "INSERT OR IGNORE INTO interactions(supplier_id,gmail_account,thread_id,message_id,tender_id,rfq_id,date,"
            "direction,from_email,to_email,subject,message_type,response_type,refusal_reason,has_attachment,short_summary,"
            "category_context,processed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (supplier_id, mailbox, thread_id, message_id, tender_id, rfq_id, date,
             str(message.get("direction") or "").upper(), from_email, to_email,
             str(message.get("subject") or ""), str(message.get("message_type") or ""),
             str(message.get("response_type") or ""), str(message.get("refusal_reason") or ""),
             int(bool(attachments)),
             summary, _json(category_context), now),
        )
        if cursor.rowcount:
            db.execute("UPDATE interactions SET interaction_id=? WHERE id=?",
                       (f"INT-{cursor.lastrowid:08d}", cursor.lastrowid))
        elif to_email:
            row = db.execute(
                "SELECT id,to_email FROM interactions WHERE gmail_account=? AND message_id=? AND supplier_id=?",
                (mailbox, message_id, supplier_id),
            ).fetchone()
            existing = {item.strip() for item in row["to_email"].split(",") if item.strip()}
            existing.update(item.strip() for item in to_email.split(",") if item.strip())
            db.execute("UPDATE interactions SET to_email=? WHERE id=?",
                       (", ".join(sorted(existing)), row["id"]))

    def _save_quotes(self, db: sqlite3.Connection, quotes: list[dict], supplier_id: str,
                     mailbox: str, message_id: str, thread_id: str, tender_id: str,
                     rfq_id: str, now: str, counts: dict) -> None:
        fields = (
            "quote_number", "quote_date", "quote_valid_until", "product_name", "brand", "model",
            "article", "quantity", "unit", "price_unit", "price_total", "currency", "vat_rate",
            "vat_included", "without_vat", "discount_percent", "discount_amount", "availability",
            "production_days", "delivery_days", "delivery_city", "delivery_cost", "delivery_included",
            "payment_terms", "prepayment_percent", "warranty", "country_origin", "manufacturer",
            "attachment_filename", "attachment_type",
        )
        for index, item in enumerate(quotes):
            if item.get("unresolved_price") is not None:
                self._review(db, "QUOTE_PARSE_ERROR", supplier_id, "",
                             str(item.get("unresolved_price")),
                             "Цена в строке есть, но нельзя определить цену за единицу или итог",
                             mailbox, message_id, thread_id, 0.5)
                counts["review_items"] += 1
                continue
            if not _value(item, "product_name", "PRODUCT_NAME") or not any(
                item.get(key) not in (None, "") for key in ("price_unit", "PRICE_UNIT", "price_total", "PRICE_TOTAL")
            ):
                self._review(db, "QUOTE_PARSE_ERROR", supplier_id, "",
                             str(item.get("product_name") or ""),
                             "В строке КП отсутствует товар или однозначная цена",
                             mailbox, message_id, thread_id, 0.5)
                counts["review_items"] += 1
                continue
            values = [_value(item, field, field.upper()) for field in fields]
            if not any(values):
                continue
            cursor = db.execute(
                "INSERT INTO quotes(supplier_id,gmail_account,thread_id,message_id,tender_id,rfq_id,line_index,"
                + ",".join(fields) + ",created_at) VALUES(" + ",".join("?" for _ in range(8 + len(fields))) + ")",
                (supplier_id, mailbox, thread_id, message_id, tender_id, rfq_id, index, *values, now),
            )
            db.execute("UPDATE quotes SET quote_id=? WHERE id=?",
                       (f"QUO-{cursor.lastrowid:08d}", cursor.lastrowid))
            counts["quotes_created"] += 1

    def _save_attachments(self, db: sqlite3.Connection, attachments: list[dict], supplier_id: str,
                          mailbox: str, message_id: str, thread_id: str, now: str,
                          counts: dict) -> None:
        for index, item in enumerate(attachments):
            attachment_id = _value(item, "attachment_id", "id") or f"part-{index}"
            cursor = db.execute(
                "INSERT OR IGNORE INTO attachments(supplier_id,gmail_account,thread_id,message_id,"
                "attachment_id,filename,mime_type,file_size,extracted_text_status,parse_error,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (supplier_id or None, mailbox, thread_id, message_id, attachment_id,
                 _value(item, "filename", "name"), _value(item, "mime_type", "type"),
                 int(item.get("file_size") or 0), _value(item, "extracted_text_status"),
                 _value(item, "parse_error"), now),
            )
            counts["attachments_created"] += cursor.rowcount

    def _review(self, db: sqlite3.Connection, type_: str, candidate_1: str,
                candidate_2: str, value: str, reason: str, mailbox: str,
                message_id: str, thread_id: str, confidence: float) -> None:
        cursor = db.execute(
            "INSERT INTO review_queue(type,supplier_candidate_1,supplier_candidate_2,value,reason,"
            "gmail_account,message_id,thread_id,confidence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (type_, candidate_1, candidate_2, value, reason, mailbox,
             message_id, thread_id, confidence, _now()),
        )
        db.execute("UPDATE review_queue SET review_id=? WHERE id=?",
                   (f"REV-{cursor.lastrowid:08d}", cursor.lastrowid))

    def rebuild_supplier(self, supplier_id: str) -> dict[str, Any]:
        """Recalculate mutable summary fields while retaining all historical facts."""
        with self._connect() as db:
            return self._rebuild_supplier(db, supplier_id)

    def _rebuild_supplier(self, db: sqlite3.Connection, supplier_id: str) -> dict[str, Any]:
        if db.execute("SELECT 1 FROM suppliers WHERE supplier_id=?", (supplier_id,)).fetchone() is None:
            raise KeyError(supplier_id)
        dates = db.execute(
            "SELECT MIN(date) AS first_date,MAX(date) AS last_date,COUNT(*) AS interactions "
            "FROM interactions WHERE supplier_id=?", (supplier_id,),
        ).fetchone()
        email = db.execute(
            "SELECT email FROM contacts WHERE supplier_id=? AND email<>'' "
            "ORDER BY is_primary DESC,last_seen_at DESC,id LIMIT 1", (supplier_id,),
        ).fetchone()
        phone = db.execute(
            "SELECT phone FROM contacts WHERE supplier_id=? AND phone<>'' "
            "ORDER BY is_primary DESC,last_seen_at DESC,id LIMIT 1", (supplier_id,),
        ).fetchone()
        alias_email = db.execute(
            "SELECT alias_value FROM aliases WHERE supplier_id=? AND alias_type='EMAIL' "
            "ORDER BY last_seen_at DESC,id LIMIT 1", (supplier_id,),
        ).fetchone()
        alias_phone = db.execute(
            "SELECT alias_value FROM aliases WHERE supplier_id=? AND alias_type='PHONE' "
            "ORDER BY last_seen_at DESC,id LIMIT 1", (supplier_id,),
        ).fetchone()
        db.execute(
            "UPDATE suppliers SET first_contact_date=COALESCE(?,''),"
            "last_contact_date=COALESCE(?,''),"
            "primary_email=?,primary_phone=?,"
            "updated_at=? WHERE supplier_id=?",
            (dates["first_date"], dates["last_date"],
             email[0] if email else alias_email[0] if alias_email else "",
             phone[0] if phone else alias_phone[0] if alias_phone else "", _now(), supplier_id),
        )
        return {"supplier_id": supplier_id, "interactions": dates["interactions"]}

    def stats(self) -> dict[str, int]:
        tables = {
            "suppliers": "suppliers", "contacts": "contacts",
            "categories": "supplier_categories", "products": "supplier_products",
            "interactions": "interactions", "quotes": "quotes",
            "attachments": "attachments", "processed_messages": "processed_messages",
            "review_queue": "review_queue", "errors": "errors",
        }
        with self._connect() as db:
            result = {key: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                      for key, table in tables.items()}
            result["messages_processed"] = result["processed_messages"]
            result["conflicts"] = int(db.execute(
                "SELECT COUNT(*) FROM review_queue WHERE type IN ('POSSIBLE_DUPLICATE','COMPANY_CONFLICT','CONTACT_CONFLICT')"
            ).fetchone()[0])
            result["duplicates_merged"] = 0
            return result

    def sheet_rows(self) -> dict[str, list[dict[str, Any]]]:
        """Return a complete Sheets projection with stable, uppercase column names."""
        from .sheet_schema import HEADERS

        with self._connect() as db:
            raw = {
                "SUPPLIERS": _rows(db.execute("SELECT * FROM suppliers WHERE supplier_id IS NOT NULL ORDER BY id")),
                "CONTACTS": _rows(db.execute("SELECT * FROM contacts ORDER BY id")),
                "SUPPLIER_CATEGORIES": _rows(db.execute("SELECT * FROM supplier_categories ORDER BY id")),
                "SUPPLIER_PRODUCTS": _rows(db.execute("SELECT * FROM supplier_products ORDER BY id")),
                "QUOTES": _rows(db.execute("SELECT * FROM quotes ORDER BY id")),
                "INTERACTIONS": _rows(db.execute("SELECT * FROM interactions ORDER BY id")),
                "ALIASES": _rows(db.execute("SELECT * FROM aliases ORDER BY id")),
                "REVIEW_QUEUE": _rows(db.execute("SELECT * FROM review_queue ORDER BY id")),
                "PROCESSING_LOG": _rows(db.execute("SELECT * FROM processing_log ORDER BY id")),
                "ATTACHMENTS": _rows(db.execute("SELECT * FROM attachments ORDER BY id")),
                "ERRORS": _rows(db.execute("SELECT * FROM errors ORDER BY id")),
            }
            grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for table in ("INTERACTIONS", "QUOTES", "SUPPLIER_CATEGORIES", "SUPPLIER_PRODUCTS"):
                groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in raw[table]:
                    groups[row["supplier_id"]].append(row)
                grouped[table] = groups
            source_mailboxes = {
                sid: ",".join(sorted({row["gmail_account"] for row in items}))
                for sid, items in grouped["INTERACTIONS"].items()
            }
            for supplier in raw["SUPPLIERS"]:
                sid = supplier["supplier_id"]
                interactions = grouped["INTERACTIONS"].get(sid, [])
                quotes = grouped["QUOTES"].get(sid, [])
                categories = grouped["SUPPLIER_CATEGORIES"].get(sid, [])
                products = grouped["SUPPLIER_PRODUCTS"].get(sid, [])
                first_category = categories[0] if categories else {}
                ordered_interactions = sorted(interactions, key=lambda x: (x["date"], x["id"]))
                dated_interactions = list(reversed(ordered_interactions))
                interaction_dates = {
                    (item["gmail_account"], item["message_id"]): item["date"]
                    for item in interactions
                }
                supplier.update({
                    "category_l1": first_category.get("category_l1", ""),
                    "category_l2": first_category.get("category_l2", ""),
                    "category_l3": first_category.get("category_l3", ""),
                    "category_l4": first_category.get("category_l4", ""),
                    "brands": ", ".join(sorted({x["brand"] for x in products if x["brand"]})),
                    "total_threads": len({x["thread_id"] for x in interactions if x["thread_id"]}),
                    "total_rfq": len({x["rfq_id"] for x in interactions if x["rfq_id"]}),
                    "total_quotes": len(quotes),
                    "total_refusals": sum(x["response_type"] == "REFUSAL" for x in interactions),
                    "last_rfq_id": next((x["rfq_id"] for x in dated_interactions if x["rfq_id"]), ""),
                    "last_tender_id": next((x["tender_id"] for x in dated_interactions if x["tender_id"]), ""),
                    "source_mailboxes": source_mailboxes.get(sid, ""),
                })
                sent: dict[str, dict[str, Any]] = {}
                responses: dict[str, dict[str, Any]] = {}
                for interaction in ordered_interactions:
                    thread_key = interaction["thread_id"] or interaction["message_id"]
                    if interaction["direction"] == "OUT" and thread_key not in sent:
                        sent[thread_key] = interaction
                    elif interaction["direction"] == "IN" and thread_key in sent and thread_key not in responses:
                        responses[thread_key] = interaction
                response_hours: list[float] = []
                for thread_key, inbound in responses.items():
                    outbound = sent[thread_key]
                    try:
                        elapsed = (datetime.fromisoformat(inbound["date"]) -
                                   datetime.fromisoformat(outbound["date"])).total_seconds() / 3600
                        if elapsed >= 0:
                            response_hours.append(elapsed)
                    except ValueError:
                        pass
                supplier.update({
                    "rfq_sent": len(sent), "rfq_replied": len(responses),
                    "response_rate": round(100 * len(responses) / len(sent), 2) if sent else "",
                    "avg_response_time_hours": round(sum(response_hours) / len(response_hours), 2)
                    if response_hours else "",
                    "last_response_date": max((x["date"] for x in interactions if x["direction"] == "IN"), default=""),
                    "last_quote_date": max((interaction_dates.get((x["gmail_account"], x["message_id"]))
                                            or x["quote_date"] or x["created_at"] for x in quotes), default=""),
                })
            for item in raw["SUPPLIER_PRODUCTS"]:
                item["thread_id"] = item["source_thread_id"]
            for item in raw["ATTACHMENTS"]:
                item["parse_error"] = item.get("parse_error", "")
            aliases = raw["ALIASES"]
            for item in aliases:
                kind = item["alias_type"]
                item.update({
                    "domain": item["alias_value"] if kind == "DOMAIN" else "",
                    "email": item["alias_value"] if kind == "EMAIL" else "",
                    "phone": item["alias_value"] if kind == "PHONE" else "",
                    "company_name": item["alias_value"] if kind == "COMPANY_NAME" else "",
                    "inn": item["alias_value"] if kind == "INN" else "",
                    "website": item["alias_value"] if kind == "WEBSITE" else "",
                })
            mapped = {}
            for name, headers in HEADERS.items():
                if name == "DASHBOARD":
                    mapped[name] = [
                        {"METRIC": key.upper(), "VALUE": value, "DETAIL": ""}
                        for key, value in self.stats().items()
                    ]
                    continue
                mapped[name] = []
                for row in raw.get(name, []):
                    upper = {key.upper(): value for key, value in row.items() if key != "id"}
                    mapped[name].append({header: upper.get(header, "") for header in headers})
            return mapped

    def find_suppliers(self, query: str) -> list[dict[str, Any]]:
        from .search import find_suppliers
        with self._connect() as db:
            return find_suppliers(db, query)

    def suggest_suppliers_for_rfq(
        self, query: str = "", *, category: str = "", product: str = "",
        brand: str = "", model: str = "", limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Find previously contacted suppliers by recorded category/product facts."""
        from .search import suggest_suppliers_for_rfq
        with self._connect() as db:
            return suggest_suppliers_for_rfq(
                db, query=query, category=category, product=product,
                brand=brand, model=model, limit=limit,
            )
