from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol, Sequence

from tender_parser.config import BROAD_SEARCH_TERMS, CATEGORY_KEYWORDS, REGION_TERMS
from tender_parser.dedup import deduplicate_tenders
from tender_parser.documents import DocumentAnalyzer
from tender_parser.enrichment import TenderEnricher
from tender_parser.env import get_env_status, load_env_file
from tender_parser.exporters.excel import export_excel, load_manual_selections, sort_for_review
from tender_parser.exporters.html_report import export_html_report
from tender_parser.exporters.json_exporter import export_json, export_run_report
from tender_parser.filters import evaluate_tender
from tender_parser.models import TenderRecord
from tender_parser.run_report import (
    SourceFetchResult,
    SourceHealth,
    flag_suspect_empty,
    load_previous_counts,
    load_previous_profile,
)
from tender_parser.sources.b2b_center import B2BCenterSource
from tender_parser.sources.composite import CompositeSource
from tender_parser.sources.eat import EatIntegrationSource
from tender_parser.sources.eis import EisZakupkiSource
from tender_parser.sources.etp_gpb import EtpGpbRssSource
from tender_parser.sources.imports import ImportFolderSource
from tender_parser.rts_accumulator import RtsAccumulator, RtsAccumulatorSource
from tender_parser.sources.rostender import RostenderSource
from tender_parser.sources.rts import RtsPublicSource, SourceFetchError
from tender_parser.sources.rts_cabinet import RtsCabinetBrowserSource
from tender_parser.sources.tender_pro import TenderProSource
from tender_parser.sources.torgi82 import Torgi82Source
from tender_parser.storage import TenderStorage


class TenderSource(Protocol):
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        ...


EAT_REQUIRED_ENV_KEYS = ["EAT_API_TOKEN", "EAT_EXT_SYSTEM"]
RunProfile = Literal["full", "fast", "local", "rts", "rts-cabinet", "rts-accumulated"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tender_parser")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "check-env", "rts-add-page", "rts-watch"],
    )
    parser.add_argument("--base-dir", default=".", help="Project directory for data and exports")
    parser.add_argument("--dry-run", action="store_true", help="Create directories and exit")
    parser.add_argument("--now", default="", help="Override current datetime for tests, ISO format")
    parser.add_argument(
        "--profile",
        default="full",
        choices=["full", "fast", "local", "rts", "rts-cabinet", "rts-accumulated"],
        help=(
            "Source profile: full, fast, local imports/documents only, RTS diagnostics, "
            "RTS cabinet browser mode, or accumulated RTS cabinet pages"
        ),
    )
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
    return build_source_for_profile("full")


def build_source_for_profile(profile: RunProfile) -> TenderSource:
    if profile == "local":
        return CompositeSource([])
    if profile == "rts":
        return CompositeSource([RtsPublicSource()])
    if profile == "rts-cabinet":
        return CompositeSource([RtsCabinetBrowserSource()])
    if profile == "fast":
        return CompositeSource(
            [
                CompositeSource(
                    [
                        TenderProSource(),
                        Torgi82Source(),
                        B2BCenterSource(),
                        EatIntegrationSource(),
                        RostenderSource(),
                    ]
                ),
            ],
            stop_after_first_success=True,
        )
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
                    RtsPublicSource(),
                ]
            ),
        ],
        stop_after_first_success=True,
    )


def _fetch_with_report(
    source: TenderSource,
    keywords: list[str],
    *,
    preserve_error_report: bool = False,
) -> SourceFetchResult:
    if isinstance(source, CompositeSource):
        result = source.fetch_with_report(keywords)
        if not result.tenders and result.errors and not preserve_error_report:
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


def _rts_add_page_command(data_dir: Path, source: TenderSource | None) -> int:
    cabinet = source or RtsCabinetBrowserSource()
    result = cabinet.fetch_with_report([])  # type: ignore[attr-defined]
    if result.errors:
        print(f"RTS кабинет недоступен: {'; '.join(result.errors)}")
        print("Накопитель не изменен.")
        return 2
    accumulator = RtsAccumulator(data_dir / "tenders.db")
    added, total = accumulator.add_many(result.tenders)
    print(f"Прочитано строк со страницы: {len(result.tenders)}")
    print(f"Новых в накопителе: {added}")
    print(f"Всего в накопителе RTS: {total}")
    print("Дальше: листайте выдачу и повторяйте, затем запустите профиль rts-accumulated.")
    return 0


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
    if args.command == "rts-add-page":
        return _rts_add_page_command(data_dir, source)
    if args.command == "rts-watch":
        from tender_parser.browser.rts_watcher import RtsCabinetWatcher

        return RtsCabinetWatcher(data_dir / "tenders.db").run_forever()
    if args.dry_run:
        return 0

    try:
        current_time = datetime.fromisoformat(args.now) if args.now else datetime.now()
    except ValueError:
        print(f"Неверный формат --now: {args.now!r}; нужен ISO, например 2026-07-04T08:00:00")
        return 2
    if source is not None:
        active_source = source
    elif args.profile == "rts-accumulated":
        active_source = RtsAccumulatorSource(data_dir / "tenders.db")
    else:
        active_source = build_source_for_profile(args.profile)
    try:
        source_result = _fetch_with_report(
            active_source,
            _all_keywords(),
            preserve_error_report=args.profile in {"rts", "rts-cabinet"},
        )
    except SourceFetchError as exc:
        print(f"Ошибка источника: {exc}")
        print("Excel и JSON не перезаписаны, предыдущий отчет сохранен.")
        return 2

    import_result = ImportFolderSource(base_dir / "imports").fetch_with_report(_all_keywords())
    source_result.tenders.extend(import_result.tenders)
    source_result.health.extend(import_result.health)
    source_result.errors.extend(import_result.errors)
    # Baseline suspect_empty сравним только с прогоном того же профиля:
    # локальный прогон не должен ни ложно флагать, ни стирать baseline full-прогона.
    if load_previous_profile(exports_dir / "run_report.json") == args.profile:
        source_result.health = flag_suspect_empty(
            source_result.health, load_previous_counts(exports_dir / "run_report.json")
        )

    raw_tenders = TenderEnricher(DocumentAnalyzer(base_dir / "documents")).enrich(source_result.tenders)
    deduplication = deduplicate_tenders(raw_tenders)
    evaluated = [evaluate_tender(tender, now=current_time) for tender in deduplication.tenders]

    # Только предпросмотр «новых»: фиксация в БД — после успешных экспортов,
    # иначе упавший экспорт навсегда теряет карточки из CRM-очереди.
    storage = TenderStorage(data_dir / "tenders.db")
    first_seen = storage.preview_new(evaluated)

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
        now=current_time,
        source_health=source_result.health,
        manual_selections=load_manual_selections(exports_dir),
        new_keys={tender.unique_key for tender in first_seen},
    )
    json_path = export_json(actionable, exports_dir / "latest.json")
    new_json_path = export_json(new_actionable, exports_dir / "new_tenders.json")
    html_path = export_html_report(
        actionable,
        exports_dir / "latest.html",
        source_report=source_result,
        raw_count=len(raw_tenders),
        unique_count=len(deduplication.tenders),
        new_count=len(new_actionable),
    )
    report_path = export_run_report(
        source_result,
        exports_dir / "run_report.json",
        raw_count=len(raw_tenders),
        unique_count=len(deduplication.tenders),
        new_count=len(new_actionable),
        profile=args.profile,
    )
    storage.upsert_many(evaluated)

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
    print(f"HTML: {html_path}")
    print(f"Новые JSON: {new_json_path}")
    print(f"Отчет источников: {report_path}")
    return 0


def main() -> int:
    return run()
