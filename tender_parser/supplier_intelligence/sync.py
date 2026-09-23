"""Resumable Gmail -> SQLite -> Google Sheets supplier synchronization."""

from __future__ import annotations

import logging
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tender_parser.supplier_intelligence.config import MailboxConfig, SupplierConfig
from tender_parser.supplier_intelligence.gmail_collector import GmailCollector, HistoryCursorExpired
from tender_parser.supplier_intelligence.message_parser import normalize_gmail_message
from tender_parser.supplier_intelligence.sheets_store import SupplierSheetsStore
from tender_parser.supplier_intelligence.storage import SupplierStore


LOG = logging.getLogger(__name__)


@dataclass
class SyncResult:
    mailboxes: dict[str, dict] = field(default_factory=dict)
    sheets: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, int]:
        keys = (
            "messages_found", "messages_processed", "suppliers_created", "suppliers_updated",
            "contacts_created", "quotes_created", "conflicts", "error_count",
        )
        return {key: sum(int(row.get(key) or 0) for row in self.mailboxes.values()) for key in keys}


class SupplierSync:
    def __init__(self, config: SupplierConfig, *, collector_factory=GmailCollector) -> None:
        self.config = config
        self.collector_factory = collector_factory

    def run(self, *, backfill: bool = False, dry_run: bool = False, mailbox: str = "") -> SyncResult:
        if not self.config.mailboxes:
            raise ValueError("No GMAIL_ACCOUNT_n configured")
        if mailbox and mailbox not in {item.address for item in self.config.mailboxes}:
            raise ValueError(f"Unknown mailbox: {mailbox}")
        result = SyncResult(dry_run=dry_run)
        with _store_path(self.config.database_path, dry_run) as path:
            store = SupplierStore(path, confidence_threshold=self.config.confidence_threshold)
            for entry in self.config.mailboxes:
                if mailbox and entry.address != mailbox:
                    continue
                started = _now()
                row = _new_run_row()
                result.mailboxes[entry.address] = row
                collector = self.collector_factory(
                    entry.address, self.config.client_secret_path, entry.token_path
                )
                try:
                    collector.authorize(interactive=False)
                    self._retry_pending(store, collector, entry.address, row)
                    if backfill or not store.get_sync_state(entry.address):
                        self._backfill(store, collector, entry.address, row, publish_partial=not dry_run)
                    else:
                        self._incremental(store, collector, entry.address, row)
                    row["status"] = "OK" if row["error_count"] == 0 else "PARTIAL"
                except Exception as exc:
                    row["status"] = "ERROR"
                    row["error_count"] += 1
                    message = f"{entry.address}: {type(exc).__name__}: {exc}"
                    result.errors.append(message)
                    LOG.exception("Mailbox sync failed: %s", entry.address)
                finally:
                    try:
                        store.record_run({
                            "run_id": str(uuid.uuid4()), "started_at": started,
                            "finished_at": _now(), "mailbox": entry.address,
                            **row, "error_text": "; ".join(result.errors)[-1000:],
                        })
                    except Exception:
                        LOG.exception("Could not record processing log")
            if not dry_run and self.config.spreadsheet_id and self.config.service_account_path.is_file():
                try:
                    result.sheets = SupplierSheetsStore(
                        self.config.spreadsheet_id, self.config.service_account_path
                    ).sync(store.sheet_rows())
                except Exception as exc:
                    result.errors.append(f"Google Sheets: {type(exc).__name__}: {exc}")
                    LOG.exception("Google Sheets projection failed")
            elif not dry_run:
                result.errors.append("Google Sheets access is not configured (SUPPLIER_SPREADSHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE)")
        return result

    def reprocess(self, message_id: str, *, mailbox: str = "") -> dict:
        if not message_id:
            raise ValueError("--message-id is required")
        store = SupplierStore(self.config.database_path, confidence_threshold=self.config.confidence_threshold)
        results: dict[str, dict] = {}
        for entry in self.config.mailboxes:
            if mailbox and entry.address != mailbox:
                continue
            collector = self.collector_factory(entry.address, self.config.client_secret_path, entry.token_path)
            collector.authorize(interactive=False)
            try:
                raw = collector.get_message(message_id)
            except Exception:
                if mailbox:
                    raise
                continue
            message = self._normalize(entry.address, raw, collector)
            results[entry.address] = store.ingest(message, force=True)
        if not results:
            raise ValueError(f"Gmail message {message_id} was not found in configured mailboxes")
        if self.config.spreadsheet_id and self.config.service_account_path.is_file():
            SupplierSheetsStore(self.config.spreadsheet_id, self.config.service_account_path).sync(store.sheet_rows())
        return results

    def _backfill(
        self, store: SupplierStore, collector: GmailCollector, mailbox: str, row: dict,
        *, publish_partial: bool = False,
    ) -> None:
        page_token = store.get_checkpoint(mailbox)
        baseline = store.get_backfill_start_history(mailbox)
        while True:
            ids, next_token, first_history = collector.list_message_ids(page_token, self.config.batch_size)
            if not baseline and first_history:
                baseline = first_history
                store.set_backfill_start_history(mailbox, baseline)
            row["messages_found"] += len(ids)
            for message_id in ids:
                self._process_one(store, collector, mailbox, message_id, row)
            store.set_checkpoint(mailbox, next_token)
            # Long historical imports must become visible in the working Sheet
            # before the entire mailbox has finished. The SQLite page checkpoint
            # is already durable, so a failed projection is safe to retry.
            if publish_partial and row["messages_processed"]:
                try:
                    self._project_sheets(store)
                except Exception:
                    LOG.exception("Partial Google Sheets projection failed")
            if not next_token:
                break
            page_token = next_token
        # Capture messages arriving while historical pages were being read.
        if baseline:
            try:
                self._history_from(store, collector, mailbox, baseline, row)
            except HistoryCursorExpired:
                # A long backfill can outlive Gmail's cursor. A second ID scan
                # skips indexed messages and leaves a fresh history cursor.
                fresh_baseline = str(collector.profile().get("historyId") or "")
                self._rescan_ids(store, collector, mailbox, row)
                if fresh_baseline:
                    store.set_sync_state(mailbox, fresh_baseline)
        else:
            store.set_sync_state(mailbox, str(collector.profile().get("historyId") or ""))
        store.set_checkpoint(mailbox, None)
        store.set_backfill_start_history(mailbox, None)

    def _incremental(self, store: SupplierStore, collector: GmailCollector, mailbox: str, row: dict) -> None:
        start = store.get_sync_state(mailbox)
        try:
            self._history_from(store, collector, mailbox, start, row)
        except HistoryCursorExpired:
            fresh_baseline = str(collector.profile().get("historyId") or "")
            self._rescan_ids(store, collector, mailbox, row)
            if fresh_baseline:
                store.set_sync_state(mailbox, fresh_baseline)

    def _history_from(
        self, store: SupplierStore, collector: GmailCollector, mailbox: str,
        start: str, row: dict,
    ) -> None:
        page_token = None
        latest = start
        while True:
            ids, next_token, history = collector.list_history(start, page_token)
            latest = history or latest
            row["messages_found"] += len(ids)
            for message_id in ids:
                self._process_one(store, collector, mailbox, message_id, row)
            if not next_token:
                break
            page_token = next_token
        # Commit the cursor after all message facts are durable.
        if latest:
            store.set_sync_state(mailbox, latest)

    def _rescan_ids(self, store: SupplierStore, collector: GmailCollector, mailbox: str, row: dict) -> None:
        page_token = None
        while True:
            ids, page_token, _ = collector.list_message_ids(page_token, self.config.batch_size)
            row["messages_found"] += len(ids)
            for message_id in ids:
                self._process_one(store, collector, mailbox, message_id, row)
            if not page_token:
                break

    def _retry_pending(self, store: SupplierStore, collector: GmailCollector, mailbox: str, row: dict) -> None:
        for message_id in store.pending_errors(mailbox):
            self._process_one(store, collector, mailbox, message_id, row)

    def _process_one(
        self, store: SupplierStore, collector: GmailCollector, mailbox: str,
        message_id: str, row: dict,
    ) -> None:
        if store.seen(mailbox, message_id):
            return
        for attempt in range(self.config.max_retries):
            try:
                raw = collector.get_message(message_id)
                parsed = self._normalize(mailbox, raw, collector)
                counts = store.ingest(parsed)
                if counts.get("status") == "error":
                    raise RuntimeError(str(counts.get("error_text") or "supplier ingestion failed"))
                store.clear_error(mailbox, message_id)
                row["messages_processed"] += 1
                for key in ("suppliers_created", "suppliers_updated", "contacts_created", "quotes_created", "conflicts"):
                    row[key] += int(counts.get(key) or 0)
                return
            except Exception as exc:
                if attempt + 1 < self.config.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                row["error_count"] += 1
                store.record_error(mailbox, message_id, f"{type(exc).__name__}: {exc}")
                LOG.exception("Message failed: %s/%s", mailbox, message_id)

    def _normalize(self, mailbox: str, raw: dict, collector: GmailCollector) -> dict:
        return normalize_gmail_message(
            mailbox, raw,
            own_emails=self.config.own_emails,
            own_domains=self.config.own_domains,
            ignored_sender_domains=self.config.ignored_sender_domains,
            ignored_sender_prefixes=self.config.ignored_sender_prefixes,
            attachment_loader=collector.get_attachment,
            attachment_max_bytes=self.config.attachment_max_bytes,
        )

    def _project_sheets(self, store: SupplierStore) -> dict[str, int]:
        if not self.config.spreadsheet_id or not self.config.service_account_path.is_file():
            return {}
        return SupplierSheetsStore(
            self.config.spreadsheet_id, self.config.service_account_path,
        ).sync(store.sheet_rows())


def _new_run_row() -> dict:
    return {
        "messages_found": 0, "messages_processed": 0, "suppliers_created": 0,
        "suppliers_updated": 0, "contacts_created": 0, "quotes_created": 0,
        "conflicts": 0, "error_count": 0, "status": "RUNNING",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _store_path:
    """Dry-run database copy; the primary state and Sheet are untouched."""

    def __init__(self, original: Path, dry_run: bool):
        self.original = original
        self.dry_run = dry_run
        self.temp: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        if not self.dry_run:
            self.original.parent.mkdir(parents=True, exist_ok=True)
            return self.original
        self.temp = tempfile.TemporaryDirectory(prefix="supplier-intelligence-dry-run-")
        copied = Path(self.temp.name) / self.original.name
        try:
            if self.original.is_file():
                # sqlite3.Connection's context manager commits/rolls back but
                # does not close file handles. Windows cannot delete the temp
                # database while the backup target is still open.
                with closing(sqlite3.connect(self.original)) as source:
                    with closing(sqlite3.connect(copied)) as target:
                        source.backup(target)
        except Exception:
            self.temp.cleanup()
            self.temp = None
            raise
        return copied

    def __exit__(self, *_: object) -> None:
        if self.temp:
            self.temp.cleanup()
