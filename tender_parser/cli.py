from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol, Sequence

from tender_parser import config
from tender_parser.dedup import deduplicate_tenders
from tender_parser.document_downloader import DocumentDownloadConfig, EisDocumentDownloader
from tender_parser.direct_links import EisCardLinkEnricher, normalize_direct_links
from tender_parser.documents import DocumentAnalyzer
from tender_parser.enrichment import TenderEnricher
from tender_parser.env import get_env_status, load_env_file
from tender_parser.exporters.excel import export_excel, load_manual_selections, sort_for_review
from tender_parser.exporters.html_report import export_html_report
from tender_parser.exporters.json_exporter import export_json, export_run_report
from tender_parser.filters import evaluate_tender
from tender_parser.google_sheets import GoogleSheetsConfig, GoogleSheetsRegistry
from tender_parser.models import TenderRecord
from tender_parser.notifications import (
    NotificationConfig,
    TelegramNotifier,
    build_daily_run_summary,
    export_notification_digest,
)
from tender_parser.run_report import (
    SourceFetchResult,
    SourceHealth,
    flag_suspect_empty,
    load_previous_counts,
    load_previous_profile,
)
from tender_parser.rostender_resolution import RostenderOfficialResolver
from tender_parser.rts_background import (
    DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    RtsSnapshotStore,
)
from tender_parser.search_config import load_and_apply_search_config
from tender_parser.sources.b2b_center import B2BCenterSource
from tender_parser.sources.composite import CompositeSource
from tender_parser.sources.crimea_small_purchases import CrimeaSmallPurchasesSource
from tender_parser.sources.eat import EatIntegrationSource
from tender_parser.sources.eis import EisZakupkiSource
from tender_parser.sources.eis_regional_xml import EisRegionalXmlSource
from tender_parser.sources.etp_gpb import EtpGpbApiSource
from tender_parser.sources.imports import ImportFolderSource
from tender_parser.rts_accumulator import RtsAccumulator, RtsAccumulatorSource
from tender_parser.sources.rostender import RostenderSource
from tender_parser.sources.roseltorg import RoseltorgSource
from tender_parser.sources.rts import RtsPublicSource, SourceFetchError
from tender_parser.sources.rts_cabinet import RtsCabinetBrowserSource
from tender_parser.sources.tender_pro import TenderProSource
from tender_parser.sources.torgi82 import Torgi82Source
from tender_parser.sources.sberbank_ast import SberbankAstSource
from tender_parser.sources.sevastopol_small_purchases import SevastopolSmallPurchasesAdapter
from tender_parser.sources.zakazrf import ZakazRfSource
from tender_parser.storage import TenderStorage
from tender_parser.suppliers import (
    SupplierCatalog,
    export_supplier_matches,
    format_supplier_matches,
)


class TenderSource(Protocol):
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        ...


EAT_REQUIRED_ENV_KEYS = ["EAT_API_TOKEN", "EAT_EXT_SYSTEM"]
RunProfile = Literal["full", "fast", "local", "rts", "rts-cabinet", "rts-accumulated"]
ACTIONABLE_PRIORITIES = {"hot", "review", "wide"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tender_parser")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=[
            "run",
            "check-env",
            "rts-add-page",
            "rts-watch",
            "rts-refresh",
            "supplier-index",
            "supplier-search",
            "customers-refresh",
        ],
    )
    parser.add_argument("--base-dir", default=".", help="Project directory for data and exports")
    parser.add_argument("--dry-run", action="store_true", help="Create directories and exit")
    parser.add_argument("--now", default="", help="Override current datetime for tests, ISO format")
    parser.add_argument("--query", default="", help="Product query for supplier-search")
    parser.add_argument("--limit", type=int, default=10, help="Maximum supplier matches")
    parser.add_argument("--supplier", default="", help="Optional supplier ID filter")
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
    term_groups = [
        *config.CATEGORY_KEYWORDS.values(),
        config.BROAD_SEARCH_TERMS,
        config.REGION_TERMS,
    ]
    for terms in term_groups:
        for term in terms:
            normalized = term.strip()
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                result.append(normalized)
    return result


def build_default_source(base_dir: Path | None = None) -> TenderSource:
    return build_source_for_profile("full", base_dir=base_dir)


def build_source_for_profile(
    profile: RunProfile,
    *,
    base_dir: Path | None = None,
) -> TenderSource:
    project_dir = (base_dir or Path.cwd()).resolve()
    eis_state_path = _project_path_from_env(
        project_dir,
        "EIS_XML_STATE_PATH",
        Path("data/eis_regional_xml_state.json"),
    )
    eis_import_dir = _project_path_from_env(
        project_dir,
        "EIS_XML_IMPORT_DIR",
        Path("imports/eis_xml"),
    )
    eis_xml_source = lambda: EisRegionalXmlSource.from_env(
        state_path=eis_state_path,
        import_dir=eis_import_dir,
    )
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
                        EtpGpbApiSource(),
                        RoseltorgSource(),
                        ZakazRfSource(),
                        SberbankAstSource(),
                        TenderProSource(),
                        Torgi82Source(),
                        CrimeaSmallPurchasesSource(),
                        SevastopolSmallPurchasesAdapter(),
                        B2BCenterSource(),
                        EatIntegrationSource(),
                        eis_xml_source(),
                        EisZakupkiSource(),
                        RostenderSource(),
                    ],
                    parallel=True,
                    max_workers=4,
                ),
            ],
            stop_after_first_success=True,
        )
    return CompositeSource(
        [
            CompositeSource(
                [
                    EtpGpbApiSource(),
                    RoseltorgSource(),
                    ZakazRfSource(),
                    SberbankAstSource(),
                    TenderProSource(),
                    Torgi82Source(),
                    CrimeaSmallPurchasesSource(),
                    SevastopolSmallPurchasesAdapter(),
                    B2BCenterSource(),
                    EatIntegrationSource(),
                    eis_xml_source(),
                    EisZakupkiSource(),
                    RostenderSource(),
                    RtsPublicSource(),
                ],
                parallel=True,
                max_workers=4,
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


def _rts_refresh_command(
    data_dir: Path,
    keywords: list[str],
    current_time: datetime,
    source: TenderSource | None,
) -> int:
    active_source = source or RtsPublicSource()
    if not hasattr(active_source, "fetch_with_report"):
        print("RTS background source must provide fetch_with_report().")
        return 2
    outcome = RtsSnapshotStore(data_dir).refresh(
        active_source,  # type: ignore[arg-type]
        keywords,
        now=current_time,
    )
    print(f"RTS background: {outcome.status}")
    print(f"Получено сейчас: {outcome.fetched_count}")
    print(f"В last-good снимке: {outcome.snapshot_count}")
    print(f"Сохранено из прошлого снимка: {outcome.preserved_count}")
    print(outcome.detail)
    return outcome.exit_code


def _project_path_from_env(
    project_dir: Path,
    env_key: str,
    default: Path,
) -> Path:
    configured = os.getenv(env_key, "").strip()
    path = Path(configured) if configured else default
    return path if path.is_absolute() else project_dir / path


def _rts_snapshot_max_age_hours() -> int:
    value = os.getenv("RTS_BACKGROUND_MAX_SNAPSHOT_AGE_HOURS", "").strip()
    if not value:
        return DEFAULT_MAX_SNAPSHOT_AGE_HOURS
    try:
        parsed = int(value)
    except ValueError:
        return DEFAULT_MAX_SNAPSHOT_AGE_HOURS
    return parsed if parsed > 0 else DEFAULT_MAX_SNAPSHOT_AGE_HOURS


def _env_enabled(name: str, *, default: bool = True) -> bool:
    value = os.getenv(name, "1" if default else "0").strip().casefold()
    return value in {"1", "true", "yes", "on", "да"}


def _resolve_rostender_records(
    tenders: list[TenderRecord],
    collected_records: list[TenderRecord],
    *,
    data_dir: Path,
) -> tuple[list[TenderRecord], SourceHealth]:
    """Hydrate only shortlisted Rostender rows from their public metadata."""

    started_at = monotonic()
    shortlist = [
        tender
        for tender in tenders
        if tender.source == "rostender"
        and tender.review_priority in ACTIONABLE_PRIORITIES
    ]
    if not _env_enabled("ROSTENDER_RESOLUTION_ENABLED"):
        return tenders, SourceHealth(
            source="rostender-resolution",
            status="skipped",
            found=0,
            elapsed_seconds=round(monotonic() - started_at, 3),
            detail=f"отключено; кандидатов {len(shortlist)}",
        )
    if not shortlist:
        return tenders, SourceHealth(
            source="rostender-resolution",
            status="empty",
            found=0,
            elapsed_seconds=round(monotonic() - started_at, 3),
            detail="подходящих карточек Ростендера нет",
        )

    configured_cache = os.getenv("ROSTENDER_RESOLUTION_CACHE_PATH", "").strip()
    cache_path = (
        Path(configured_cache)
        if configured_cache
        else Path("data/rostender_resolution_cache.json")
    )
    if not cache_path.is_absolute():
        cache_path = data_dir.parent / cache_path
    resolver = RostenderOfficialResolver(cache_path=cache_path)
    resolved_shortlist = resolver.resolve_shortlist(shortlist, collected_records)
    resolved_by_key = {tender.unique_key: tender for tender in resolved_shortlist}
    enriched = [resolved_by_key.get(tender.unique_key, tender) for tender in tenders]

    resolved_count = sum(bool(item.official_number) for item in resolved_shortlist)
    errors = [item for item in resolver.last_results if item.error]
    status = "partial" if errors else "ok"
    detail = (
        f"официальный номер найден у {resolved_count}/{len(shortlist)}; "
        f"сетевых/проверочных ошибок {len(errors)}"
    )
    return enriched, SourceHealth(
        source="rostender-resolution",
        status=status,
        found=resolved_count,
        elapsed_seconds=round(monotonic() - started_at, 3),
        detail=detail,
    )


def run(argv: Sequence[str] | None = None, source: TenderSource | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    data_dir, exports_dir = ensure_dirs(base_dir)
    load_env_file(base_dir / ".env")
    load_env_file(base_dir / ".env.local")
    if args.command == "check-env":
        return _check_env_command(base_dir)
    if args.command == "rts-add-page":
        return _rts_add_page_command(data_dir, source)
    if args.command == "rts-watch":
        from tender_parser.browser.rts_watcher import RtsCabinetWatcher

        return RtsCabinetWatcher(data_dir / "tenders.db").run_forever()
    if args.command in {"supplier-index", "supplier-search"}:
        catalog = SupplierCatalog(base_dir / "supplier_catalog")
        status = catalog.refresh(force=args.command == "supplier-index")
        if args.command == "supplier-index":
            print(
                f"Каталог поставщиков: {status.status}; товаров {status.product_count}, "
                f"поставщиков {status.supplier_count}, файлов {status.file_count}"
            )
            if status.detail:
                print(f"Предупреждение: {status.detail}")
            return 2 if status.status == "error" else 0
        query = args.query.strip()
        if not query:
            print("Для supplier-search укажите --query, например: --query \"шкаф архивный\"")
            return 2
        matches = catalog.search(
            query,
            limit=args.limit,
            supplier_id=args.supplier.strip().casefold() or None,
        )
        print(format_supplier_matches(query, matches))
        return 0
    if args.command == "customers-refresh":
        try:
            current_time = datetime.fromisoformat(args.now) if args.now else datetime.now()
        except ValueError:
            print(f"Неверный формат --now: {args.now!r}; нужен ISO")
            return 2
        storage = TenderStorage(data_dir / "tenders.db")
        customer_candidates = storage.fetch_customer_candidates()
        result = GoogleSheetsRegistry(
            GoogleSheetsConfig.from_env(base_dir)
        ).sync_customers(
            customer_candidates,
            generated_at=current_time,
            max_fetches=max(1, args.limit),
        )
        print(
            "Потенциальные заказчики: "
            f"{result.status}; организаций {result.customer_count}, "
            f"с контактами {result.rows_with_contacts}, "
            f"проверено ЕИС {result.fetched}, дополнено {result.enriched}, "
            f"ошибок {result.errors}"
        )
        if result.detail:
            print(result.detail)
        return 2 if result.status == "error" else 0
    configured_path = os.getenv("SEARCH_CONFIG_PATH", "").strip()
    search_config_path = Path(configured_path) if configured_path else base_dir / "config" / "Настройки_поиска.xlsx"
    if not search_config_path.is_absolute():
        search_config_path = base_dir / search_config_path
    search_config_status = load_and_apply_search_config(search_config_path)
    if search_config_status.status == "loaded":
        print(f"Словарь Excel: загружен ({search_config_status.detail})")
    elif search_config_status.status == "error":
        print(
            f"Словарь Excel: ошибка ({search_config_status.detail}); "
            "используется встроенный словарь"
        )
    else:
        print("Словарь Excel: файл не найден; используется встроенный словарь")
    if args.dry_run:
        return 0

    try:
        current_time = datetime.fromisoformat(args.now) if args.now else datetime.now()
    except ValueError:
        print(f"Неверный формат --now: {args.now!r}; нужен ISO, например 2026-07-04T08:00:00")
        return 2
    if args.command == "rts-refresh":
        return _rts_refresh_command(data_dir, _all_keywords(), current_time, source)
    if source is not None:
        active_source = source
    elif args.profile == "rts-accumulated":
        active_source = RtsAccumulatorSource(data_dir / "tenders.db")
    else:
        active_source = build_source_for_profile(args.profile, base_dir=base_dir)
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

    if args.profile == "fast":
        rts_snapshot = RtsSnapshotStore(data_dir).load_for_fast_run(
            now=current_time,
            max_age_hours=_rts_snapshot_max_age_hours(),
        )
        source_result.tenders.extend(rts_snapshot.tenders)
        source_result.health.extend(rts_snapshot.health)
        source_result.errors.extend(rts_snapshot.errors)

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

    storage = TenderStorage(data_dir / "tenders.db")
    enriched_tenders = TenderEnricher(DocumentAnalyzer(base_dir / "documents")).enrich(
        source_result.tenders
    )
    raw_tenders = storage.merge_with_history(enriched_tenders)
    preliminary_deduplication = deduplicate_tenders(raw_tenders)
    preliminary_evaluated = [
        evaluate_tender(tender, now=current_time)
        for tender in preliminary_deduplication.tenders
    ]
    if any(tender.source == "rostender" for tender in raw_tenders):
        resolved_tenders, resolution_health = _resolve_rostender_records(
            preliminary_evaluated,
            raw_tenders,
            data_dir=data_dir,
        )
        source_result.health.append(resolution_health)
    else:
        resolved_tenders = preliminary_evaluated
    # Official-number joins may now replace an aggregator duplicate with the
    # already collected official EIS/ETP record. Re-run the cheap in-memory
    # deduplication once after resolution, then evaluate the final records.
    deduplication = deduplicate_tenders(resolved_tenders)
    evaluated = [evaluate_tender(tender, now=current_time) for tender in deduplication.tenders]
    # Every native EIS/ETP row must expose a usable direct destination.  For
    # actionable EIS cards we additionally verify that the notice exists and
    # discover the actual trading platform shown by EIS.  This also prevents a
    # number printed only by an aggregator from becoming a broken "official"
    # link in the registry.
    link_enricher = EisCardLinkEnricher()
    evaluated = link_enricher.enrich(normalize_direct_links(evaluated))

    # Только предпросмотр «новых»: фиксация в БД — после успешных экспортов,
    # иначе упавший экспорт навсегда теряет карточки из CRM-очереди.
    first_seen = storage.preview_new(evaluated)

    hot = [tender for tender in evaluated if tender.review_priority == "hot"]
    review = [tender for tender in evaluated if tender.review_priority == "review"]
    wide = [tender for tender in evaluated if tender.review_priority == "wide"]
    excluded = [tender for tender in evaluated if tender.review_priority == "excluded"]
    actionable = sort_for_review(hot + review + wide)
    new_actionable = sort_for_review(
        [tender for tender in first_seen if tender.review_priority in {"hot", "review", "wide"}]
    )

    supplier_catalog = SupplierCatalog(base_dir / "supplier_catalog")
    supplier_catalog.refresh()
    supplier_path, supplier_tender_count = export_supplier_matches(
        supplier_catalog,
        actionable,
        exports_dir / "supplier_matches.json",
    )

    download_report = EisDocumentDownloader(DocumentDownloadConfig.from_env()).download(
        new_actionable,
        base_dir / "downloads",
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
    notification_path = export_notification_digest(
        new_actionable, exports_dir / "notification.txt"
    )
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
    google_result = GoogleSheetsRegistry(
        GoogleSheetsConfig.from_env(base_dir)
    ).sync(
        actionable,
        new_actionable,
        source_result,
        generated_at=current_time,
        profile=args.profile,
        raw_count=len(raw_tenders),
        unique_count=len(deduplication.tenders),
        customer_candidates=list(
            {
                tender.unique_key: tender
                for tender in [*storage.fetch_customer_candidates(), *evaluated]
            }.values()
        ),
    )
    notification_config = NotificationConfig.from_env()
    storage.upsert_many(
        evaluated,
        notification_candidates=new_actionable if notification_config.enabled else None,
    )
    notification_result = TelegramNotifier(notification_config).notify(
        storage.fetch_pending_notifications()
    )
    if notification_result.status == "sent":
        pending = storage.fetch_pending_notifications()
        storage.mark_notifications_sent([tender.unique_key for tender in pending])
    elif notification_result.status == "error":
        pending = storage.fetch_pending_notifications()
        storage.mark_notification_error(
            [tender.unique_key for tender in pending], notification_result.detail
        )

    daily_result = None
    document_result = None
    notifier = TelegramNotifier(notification_config)
    if notification_config.enabled and notification_config.send_daily_report:
        source_ok = sum(
            item.status in {"ok", "empty"} for item in source_result.health
        )
        summary_text = build_daily_run_summary(
            active_count=len(actionable),
            new_count=len(new_actionable),
            source_ok=source_ok,
            source_total=len(source_result.health),
            google_status=google_result.status,
        )
        daily_result = notifier.send_text(summary_text, buttons=True)
        if notification_config.send_excel:
            document_result = notifier.send_document(
                excel_path,
                caption=(
                    f"Отчёт за {current_time.strftime('%d.%m.%Y')}: "
                    f"новых {len(new_actionable)}, актуальных {len(actionable)}"
                ),
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
    print(f"HTML: {html_path}")
    print(f"Новые JSON: {new_json_path}")
    print(f"Текст уведомления: {notification_path}")
    print(
        f"Прайсы поставщиков: {supplier_catalog.last_status.status}, "
        f"товаров {supplier_catalog.last_status.product_count}, "
        f"совпадений с тендерами {supplier_tender_count}; {supplier_path}"
    )
    if notification_result.status == "sent":
        print(f"Telegram: отправлено {notification_result.sent_count}")
    elif notification_result.status == "error":
        print(f"Telegram: ошибка {notification_result.detail}; очередь сохранена")
    elif notification_result.status == "disabled":
        print("Telegram: не настроен")
    if daily_result is not None:
        print(f"Telegram-сводка: {daily_result.status} {daily_result.detail}")
    if document_result is not None:
        print(f"Telegram-Excel: {document_result.status} {document_result.detail}")
    print(f"Google Sheets: {google_result.status} {google_result.detail}")
    print(
        "Прямые ссылки: "
        f"проверено {link_enricher.last_report.checked}, "
        f"подтверждено {link_enricher.last_report.confirmed}, "
        f"снято ошибочных {link_enricher.last_report.invalidated}, "
        f"ошибок сети {len(link_enricher.last_report.errors)}"
    )
    if download_report.status == "disabled":
        print("Документы ЕИС: автозагрузка отключена")
    else:
        print(
            f"Документы ЕИС: скачано {download_report.downloaded_count}, "
            f"уже было {download_report.skipped_count}, ошибок {len(download_report.errors)}"
        )
    print(f"Отчет источников: {report_path}")
    delivery_failed = any(
        result is not None and result.status == "error"
        for result in [notification_result, daily_result, document_result]
    )
    return 2 if delivery_failed or google_result.status == "error" else 0


def main() -> int:
    return run()
