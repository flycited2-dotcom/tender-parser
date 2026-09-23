"""Command line entry point for the supplier register."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from tender_parser.env import load_env_file
from tender_parser.supplier_intelligence.config import SupplierConfig
from tender_parser.supplier_intelligence.gmail_collector import GmailCollector
from tender_parser.supplier_intelligence.sheets_store import SupplierSheetsStore
from tender_parser.supplier_intelligence.storage import SupplierStore
from tender_parser.supplier_intelligence.sync import SupplierSync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supplier Intelligence: Gmail -> supplier database")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backfill", "sync"):
        sub = commands.add_parser(name)
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--account", default="")
    auth = commands.add_parser("auth", help="Authorize a Gmail mailbox with read-only OAuth")
    auth.add_argument("--account", required=True)
    reprocess = commands.add_parser("reprocess")
    reprocess.add_argument("--message-id", required=True)
    reprocess.add_argument("--account", default="")
    rebuild = commands.add_parser("rebuild-supplier")
    rebuild.add_argument("supplier_id")
    commands.add_parser("stats")
    find = commands.add_parser("find")
    find.add_argument("query")
    suggest = commands.add_parser("suggest")
    suggest.add_argument("--product-name", required=True)
    suggest.add_argument("--brand", default="")
    suggest.add_argument("--model", default="")
    suggest.add_argument("--category", default="")
    args = parser.parse_args(argv)
    base_dir = Path(__file__).resolve().parents[2]
    load_env_file(base_dir / ".env")
    load_env_file(base_dir / ".env.local")
    config = SupplierConfig.from_env(base_dir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "auth":
            entry = next((item for item in config.mailboxes if item.address == args.account.casefold()), None)
            if entry is None:
                raise ValueError("Unknown --account; configure GMAIL_ACCOUNT_n first")
            collector = GmailCollector(entry.address, config.client_secret_path, entry.token_path)
            profile = collector.authorize(interactive=True)
            _output({"authorized": profile.get("emailAddress"), "token_path": str(entry.token_path)})
            return 0
        if args.command in {"backfill", "sync"}:
            result = SupplierSync(config).run(
                backfill=args.command == "backfill", dry_run=args.dry_run, mailbox=args.account,
            )
            _output({"dry_run": result.dry_run, "mailboxes": result.mailboxes,
                     "totals": result.totals(), "sheets_changed_rows": result.sheets, "errors": result.errors})
            return 2 if result.errors else 0
        store = SupplierStore(config.database_path, confidence_threshold=config.confidence_threshold)
        if args.command == "stats":
            _output(store.stats())
            return 0
        if args.command == "find":
            _output(store.find_suppliers(args.query))
            return 0
        if args.command == "suggest":
            _output(store.suggest_suppliers_for_rfq(
                product=args.product_name, brand=args.brand, model=args.model, category=args.category,
            ))
            return 0
        if args.command == "reprocess":
            _output(SupplierSync(config).reprocess(args.message_id, mailbox=args.account))
            return 0
        if args.command == "rebuild-supplier":
            rebuilt = store.rebuild_supplier(args.supplier_id)
            if config.spreadsheet_id and config.service_account_path.is_file():
                SupplierSheetsStore(config.spreadsheet_id, config.service_account_path).sync(store.sheet_rows())
            _output(rebuilt)
            return 0
    except Exception as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")
    return 2


def _output(value: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
