from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Protocol, Sequence

from tender_parser.config import BROAD_SEARCH_TERMS, CATEGORY_KEYWORDS, REGION_TERMS
from tender_parser.dedup import deduplicate_tenders
from tender_parser.env import get_env_status, load_env_file
from tender_parser.exporters.excel import export_excel, sort_for_review
from tender_parser.exporters.json_exporter import export_json, export_run_report
from tender_parser.filters import evaluate_tender
from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.b2b_center import B2BCenterSource
from tender_parser.sources.composite import CompositeSource
from tender_parser.sources.eat import EatIntegrationSource
from tender_parser.sources.eis import EisZakupkiSource
from tender_parser.sources.etp_gpb import EtpGpbRssSource
from tender_parser.sources.rostender import RostenderSource
from tender_parser.sources.rts import RtsPublicSource, SourceFetchError
from tender_parser.sources.tender_pro import TenderProSource
from tender_parser.sources.torgi82 import Torgi82Source
from tender_parser.storage import TenderStorage


class TenderSource(Protocol):
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        ...


EAT_REQUIRED_ENV_KEYS = ["EAT_API_TOKEN", "EAT_EXT_SYSTEM"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tender_parser")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "check-env"])
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
        [
            CompositeSource(
                [
                    EtpGpbRssSource(),
                    TenderProSource(),
                    Torgi82Source(),
                    B2BCenterSource(),
                    EatIntegrationSource(),
                    EisZakupkiSource(),
                    RostenderSource(),
                ]
            ),
            RtsPublicSource(),
        ],
        stop_after_first_success=True,
    )


def _fetch_with_report(source: TenderSource, keywords: list[str]) -> SourceFetchResult:
    if isinstance(source, CompositeSource):
        result = source.fetch_with_report(keywords)
        if not result.tenders and result.errors:
            raise SourceFetchError(f"все источники недоступны: {'; '.join(result.errors)}")
        return result

    started_at = monotonic()
    tenders = source.fetch_keywords(keywords)
    return SourceFetchResult(
        tenders=tenders,
        health=[
            SourceHealth(
                source=source.__class__.__name__,
                status="ok" if tenders else "empty",
                found=len(tenders),
                elapsed_seconds=round(monotonic() - started_at, 3),
            )
        ],
    )


def _check_env_command(base_dir: Path) -> int:
    status = get_env_status(EAT_REQUIRED_ENV_KEYS)
    print(f"Config file: {base_dir / '.env'}")
    for key, configured in status.items():
        state = "configured" if configured else "missing"
        print(f"{key}: {state}")
    return 0 if all(status.values()) else 1


def run(argv: Sequence[str] | None = None, source: TenderSource | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    data_dir, exports_dir = ensure_dirs(base_dir)
    load_env_file(base_dir / ".env")
    if args.command == "check-env":
        return _check_env_command(base_dir)
    if args.dry_run:
        return 0

    current_time = datetime.fromisoformat(args.now) if args.now else datetime.now()
    active_source = source or build_default_source()
    try:
        source_result = _fetch_with_report(active_source, _all_keywords())
    except SourceFetchError as exc:
        print(f"Ошибка источника: {exc}")
        print("Excel и JSON не перезаписаны, предыдущий отчет сохранен.")
        return 2

    raw_tenders = source_result.tenders
    deduplication = deduplicate_tenders(raw_tenders)
    evaluated = [evaluate_tender(tender, now=current_time) for tender in deduplication.tenders]

    storage = TenderStorage(data_dir / "tenders.db")
    first_seen = storage.upsert_many(evaluated)

    hot = [tender for tender in evaluated if tender.review_priority == "hot"]
    review = [tender for tender in evaluated if tender.review_priority == "review"]
    wide = [tender for tender in evaluated if tender.review_priority == "wide"]
    excluded = [tender for tender in evaluated if tender.review_priority == "excluded"]
    actionable = sort_for_review(hot + review + wide)
    new_actionable = sort_for_review(
        [tender for tender in first_seen if tender.review_priority in {"hot", "review", "wide"}]
    )

    date_stamp = current_time.strftime("%Y-%m-%d")
    excel_path = export_excel(
        hot,
        review,
        wide,
        excluded,
        exports_dir / f"tenders_{date_stamp}.xlsx",
        new_tenders=new_actionable,
    )
    json_path = export_json(actionable, exports_dir / "latest.json")
    new_json_path = export_json(new_actionable, exports_dir / "new_tenders.json")
    report_path = export_run_report(
        source_result,
        exports_dir / "run_report.json",
        raw_count=len(raw_tenders),
        unique_count=len(deduplication.tenders),
        new_count=len(new_actionable),
    )

    print(f"Найдено: {len(raw_tenders)}")
    print(f"После дедупликации: {len(deduplication.tenders)}")
    print(f"Горячие: {len(hot)}")
    print(f"На проверку: {len(review)}")
    print(f"Широкий хвост: {len(wide)}")
    print(f"Отсеянные: {len(excluded)}")
    print(f"Новые для CRM: {len(new_actionable)}")
    for health in source_result.health:
        detail = f"; {health.detail}" if health.detail else ""
        print(
            f"Источник {health.source}: {health.status}, {health.found} шт., "
            f"{health.elapsed_seconds:.1f} сек.{detail}"
        )
    print(f"Excel: {excel_path}")
    print(f"JSON: {json_path}")
    print(f"Новые JSON: {new_json_path}")
    print(f"Отчет источников: {report_path}")
    return 0


def main() -> int:
    return run()
