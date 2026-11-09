"""Rebuild the normal report from saved collection inputs, without polling ETPs."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from tender_parser.customers import build_customer_registry
from tender_parser.customer_contacts import CustomerContactEnricher
from tender_parser.dedup import deduplicate_tenders
from tender_parser.direct_links import normalize_direct_links
from tender_parser.exporters.excel import export_excel, load_customer_rows, load_manual_selections, sort_for_review
from tender_parser.exporters.html_report import export_html_report
from tender_parser.exporters.json_exporter import export_json, export_run_report
from tender_parser.filters import evaluate_tender, target_region
from tender_parser.google_sheets import GoogleSheetsConfig, GoogleSheetsRegistry
from tender_parser.notifications import NotificationConfig, TelegramNotifier
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.rts_sections import SECTION_IDS, _decode, _encode, load_saved_sections
from tender_parser.storage import TenderStorage


def _read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def _baseline(base_dir: Path, storage: TenderStorage) -> tuple[list, list[SourceHealth]]:
    # Pin the original daily batch. Repeated rebuilds must not replace it with
    # only the newly inserted RTS rows, nor relabel historical source data fresh.
    receipt = _read_json(base_dir/'data/collection_result.json')
    collection_id = receipt.get('run_id')
    path = base_dir/'data/report_refresh_baseline.json'
    saved = _read_json(path)
    report = _read_json(base_dir/'exports/run_report.json')
    reuse = (collection_id and saved.get('collection_run_id') == collection_id) or (
        not collection_id and saved.get('items') and report.get('profile')=='report-from-cache')
    if reuse:
        rows = [_decode(obj) for obj in saved.get('items', [])]
        health = [SourceHealth(**obj) for obj in saved.get('health', [])]
        return rows, health
    rows = storage.fetch_latest_batch()
    health = []
    for obj in report.get('sources', []):
        item = SourceHealth(**obj)
        if item.source not in SECTION_IDS:
            health.append(replace(item, detail=f'previous collection {report.get("generated_at", "unknown")}; not re-polled; {item.detail}'))
    _write_json(path, {'collection_run_id': collection_id, 'items': [_encode(t) for t in rows],
                       'health': [asdict(h) for h in health]})
    return rows, health


def refresh_saved_report(base_dir: Path, *, now: datetime | None = None, publish: bool = True) -> dict:
    current = now or datetime.now()
    exports = base_dir/'exports'
    storage = TenderStorage(base_dir/'data/tenders.db')
    baseline, health = _baseline(base_dir, storage)
    rts = load_saved_sections(base_dir, now=current)
    inputs = {t.unique_key: t for t in [*baseline, *rts.tenders]}
    evaluated = normalize_direct_links([
        evaluate_tender(t, now=current) for t in deduplicate_tenders(list(inputs.values())).tenders
    ])
    fresh = [t for t in storage.preview_new(evaluated) if t.review_priority in {'hot','review','wide'}]
    groups = {p: [t for t in evaluated if t.review_priority==p] for p in ('hot','review','wide','excluded')}
    actionable = sort_for_review(groups['hot']+groups['review']+groups['wide'], now=current)
    history = {t.unique_key: t for t in [*storage.fetch_all_tenders(), *evaluated]}
    regional = sort_for_review([t for t in history.values() if target_region(t)], now=current)
    contacts = build_customer_registry(evaluated, load_customer_rows(exports))
    contacts, _ = CustomerContactEnricher(base_dir/'data/customer_contacts.json', max_fetches=0, now=lambda:current).enrich(contacts,evaluated)
    source_result = SourceFetchResult(tenders=list(inputs.values()),health=health+rts.health)
    path = exports/f'tenders_{current:%Y-%m-%d}.xlsx'
    backup = base_dir/'data/maintenance'/f'report-refresh-{current:%Y%m%d-%H%M%S-%f}'
    backup.mkdir(parents=True, exist_ok=False)
    for previous in [path, exports/'run_report.json', exports/'latest.json', exports/'new_tenders.json', exports/'latest.html']:
        if previous.exists():
            shutil.copy2(previous,backup/previous.name)
    pending = exports/f'.tenders_refresh_{current:%Y%m%d-%H%M%S-%f}.xlsx'
    export_excel(groups['hot'],groups['review'],groups['wide'],groups['excluded'],pending,
                 now=current,new_tenders=fresh,source_health=source_result.health,
                 regional_tenders=regional,customer_rows=contacts,
                 manual_selections=load_manual_selections(exports),new_keys={t.unique_key for t in fresh})
    try:
        pending.replace(path)
    except PermissionError:
        # The owner may have the old report open in Excel. Preserve it and
        # publish a new timestamped file instead of failing the whole rebuild.
        path=exports/f'tenders_{current:%Y-%m-%d}_refresh_{current:%H%M%S}.xlsx'
        pending.replace(path)
    export_json(actionable,exports/'latest.json')
    export_json(fresh,exports/'new_tenders.json')
    export_html_report(actionable,exports/'latest.html',source_report=source_result,raw_count=len(inputs),unique_count=len(evaluated),new_count=len(fresh))
    export_run_report(source_result,exports/'run_report.json',raw_count=len(inputs),unique_count=len(evaluated),new_count=len(fresh),profile='report-from-cache')
    # Retain all raw collected topics, including excluded rows. No mail campaigns.
    rts_evaluated = normalize_direct_links([evaluate_tender(t,now=current) for t in rts.tenders])
    storage.upsert_many(rts_evaluated)
    result = {'excel_path':str(path),'raw_count':len(inputs),'unique_count':len(evaluated),
              'active_count':len(actionable),'excluded_count':len(groups['excluded']),
              'new_count':len(fresh),'regional_count':len(regional),'backup':str(backup),
              'google_status':'not_requested','telegram_status':'not_requested'}
    if publish:
        config = replace(GoogleSheetsConfig.from_env(base_dir),customer_max_per_run=0)
        google = GoogleSheetsRegistry(config).sync(actionable,fresh,source_result,
            generated_at=current,profile='report-from-cache',raw_count=len(inputs),unique_count=len(evaluated),
            customer_candidates=evaluated,regional_tenders=regional)
        notifier = TelegramNotifier(NotificationConfig.from_env())
        telegram = notifier.send_document(path,caption=(
            f'Обновлённый отчёт с разделами РТС за {current:%d.%m.%Y}. '
            f'Актуальных: {len(actionable)}, отсеянных: {len(groups["excluded"])}. '
            'Пересобран из сохранённых данных; остальные площадки повторно не опрашивались.'
        ))
        result.update(google_status=google.status,telegram_status=telegram.status)
    _write_json(base_dir/'data/report_refresh_result.json',result)
    return result
