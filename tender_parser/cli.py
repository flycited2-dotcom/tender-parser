from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

from tender_parser.config import BROAD_SEARCH_TERMS, CATEGORY_KEYWORDS, REGION_TERMS
from tender_parser.exporters.excel import export_excel
from tender_parser.exporters.json_exporter import export_json
from tender_parser.filters import evaluate_tender
from tender_parser.models import TenderRecord
from tender_parser.sources.composite import CompositeSource
from tender_parser.sources.rostender import RostenderSource
from tender_parser.sources.rts import RtsPublicSource, SourceFetchError
from tender_parser.storage import TenderStorage


class TenderSource(Protocol):
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tender_parser")
    parser.add_argument("command", nargs="?", default="run", choices=["run"])
    parser.add_argument("--base-dir", default=".", help="Project directory for data and exports")
    parser.add_argument("--dry-run", action="store_true", help="Create directories and exit")
    parser.add_argument("--now", default="", help="Override current datetime for tests, ISO format")
    return parser


def ensure_dirs(base_dir: Path) -> tuple[Path, Path]:
    data_dir = base_dir / "data"
    exports_dir = base_dir / "exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, exports_dir


def _all_keywords() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    term_groups = [*CATEGORY_KEYWORDS.values(), BROAD_SEARCH_TERMS, REGION_TERMS]
    for terms in term_groups:
        for term in terms:
            normalized = term.strip()
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                result.append(normalized)
    return result


def build_default_source() -> TenderSource:
    return CompositeSource(
        [RostenderSource(), RtsPublicSource()],
        stop_after_first_success=True,
    )


def run(argv: Sequence[str] | None = None, source: TenderSource | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    data_dir, exports_dir = ensure_dirs(base_dir)
    if args.dry_run:
        return 0

    current_time = datetime.fromisoformat(args.now) if args.now else datetime.now()
    active_source = source or build_default_source()
    try:
        raw_tenders = active_source.fetch_keywords(_all_keywords())
    except SourceFetchError as exc:
        print(f"Ошибка источника: {exc}")
        print("Excel и JSON не перезаписаны, предыдущий отчет сохранен.")
        return 2

    evaluated = [evaluate_tender(tender, now=current_time) for tender in raw_tenders]

    storage = TenderStorage(data_dir / "tenders.db")
    storage.upsert_many(evaluated)

    matched = [tender for tender in evaluated if tender.filter_status == "matched"]
    review = [tender for tender in evaluated if tender.filter_status == "review"]
    excluded = [tender for tender in evaluated if tender.filter_status == "excluded"]
    actionable = matched + review

    date_stamp = current_time.strftime("%Y-%m-%d")
    excel_path = export_excel(matched, review, excluded, exports_dir / f"tenders_{date_stamp}.xlsx")
    json_path = export_json(actionable, exports_dir / "latest.json")

    print(f"Найдено: {len(raw_tenders)}")
    print(f"Подходящие: {len(matched)}")
    print(f"На проверку: {len(review)}")
    print(f"Отсеянные: {len(excluded)}")
    print(f"Excel: {excel_path}")
    print(f"JSON: {json_path}")
    return 0


def main() -> int:
    return run()
