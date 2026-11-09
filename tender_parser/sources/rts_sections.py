"""Four separate RTS listings, collected through the owner's local Chromium."""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, fields, replace
from datetime import datetime
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlparse

from tender_parser.filters import target_region
from tender_parser.models import TenderRecord
from tender_parser.rts_accumulator import RtsAccumulator
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.rts_cabinet import parse_cabinet_page
from tender_parser.sources.rts_market_modern import parse_modern_market_page
from tender_parser.sources.rts_poisk import parse_poisk_page


SECTION_IDS = ('rts-market-modern', 'rts-223', 'rts-44', 'rts-poisk')
MAX_CACHE_AGE_HOURS = 30


class RtsCollectionBusy(OSError):
    pass


@contextmanager
def _collection_lock(base_dir: Path):
    """Cross-session lock: a bot click must not paginate a scheduled run's tabs."""
    path = base_dir / 'data' / 'rts_sections.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RtsCollectionBusy('Another RTS section collection owns the lock') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _parse_section_rows(source: str, html: str, url: str, now: datetime) -> list[TenderRecord]:
    if source == 'rts-market-modern':
        items = parse_modern_market_page(html, url, now=now)
    elif source == 'rts-poisk':
        items = parse_poisk_page(html, url)
    elif source in {'rts-223', 'rts-44'}:
        items = [replace(t, source=source, platform_url=t.url, platform_number=t.tender_number,
                         procurement_law='44-ФЗ' if source == 'rts-44' else '223-ФЗ')
                 for t in parse_cabinet_page(html, url)]
    else:
        return []
    return items


def _regional_rows(source: str, url: str, items: list[TenderRecord]) -> list[TenderRecord]:
    # ALL topics/prices are retained. Topic exclusion happens in evaluate_tender/Excel.
    selected = parse_qs(urlparse(url).query).get('regs', [])
    regional_market = (source == 'rts-market-modern' and selected
                       and set(selected) <= {'9100000000000', '9200000000000', '9000000000000', '9500000000000'})
    # The site's regional filter may return a card without a printed delivery region.
    # Retain it for Excel review rather than guessing a city or silently dropping it.
    return [t for t in items if target_region(t) or (regional_market and not t.region)]


def parse_section(source: str, html: str, url: str, now: datetime) -> list[TenderRecord]:
    return _regional_rows(source,url,_parse_section_rows(source,html,url,now))


def _encode(tender: TenderRecord) -> dict:
    obj = asdict(tender)
    for name in ('deadline', 'published_at', 'discovered_at'):
        obj[name] = obj[name].isoformat() if obj[name] else None
    return obj


def _decode(obj: dict) -> TenderRecord:
    values = {f.name: obj[f.name] for f in fields(TenderRecord) if f.name in obj}
    for name in ('deadline', 'published_at', 'discovered_at'):
        values[name] = datetime.fromisoformat(values[name]) if values.get(name) else None
    return TenderRecord(**values)


def _cached_rows(snapshot: dict, now: datetime) -> list[TenderRecord]:
    rows = []
    for obj in snapshot.get('items', []):
        try:
            observed = datetime.fromisoformat(obj.get('observed_at') or snapshot['captured_at'])
            age = (now - observed).total_seconds() / 3600
            if 0 <= age <= MAX_CACHE_AGE_HOURS:
                rows.append(_decode(obj))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return rows


def _merge_snapshot(old: dict, current: list[TenderRecord], checked_at: str, *, complete: bool) -> list[dict]:
    """Never erase earlier pages on a partial pass or renew an unseen row's age."""
    merged = {}
    if not complete:
        for obj in old.get('items', []):
            try:
                encoded = dict(obj)
                encoded['observed_at'] = obj.get('observed_at') or old.get('captured_at')
                merged[_decode(obj).unique_key] = encoded
            except (TypeError, ValueError, AttributeError):
                continue
    for tender in current:
        encoded = _encode(tender)
        encoded['observed_at'] = checked_at
        merged[tender.unique_key] = encoded
    return list(merged.values())


def load_saved_sections(base_dir: Path, *, now: datetime | None = None) -> SourceFetchResult:
    """Read-only report input: no browser launch, collection or timestamp updates."""
    current = now or datetime.now()
    try:
        sections = json.loads((base_dir / 'data/rts_sections_state.json').read_text(encoding='utf-8'))['sections']
        if not isinstance(sections, dict):
            raise ValueError('invalid sections')
    except (OSError, ValueError, KeyError, TypeError):
        sections = {}
    result = SourceFetchResult()
    for source in SECTION_IDS:
        snapshot = sections.get(source, {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        rows = _cached_rows(snapshot, current)
        status = snapshot.get('status', 'skipped')
        if status not in {'ok', 'empty', 'partial', 'blocked', 'skipped', 'error'}:
            status = 'error'
        if not rows and snapshot.get('items'):
            status = 'skipped'
        detail = (f'saved listing; checked={snapshot.get("last_checked_at", "never")}; '
                  f'last_complete={snapshot.get("last_complete_at") or "never"}; '
                  f'no browser requests during report rebuild; {snapshot.get("detail", "no saved listing")}')
        result.tenders.extend(rows)
        result.health.append(SourceHealth(source, status, len(rows), 0.0, detail))
    return result


class RtsSectionsClient:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path.cwd()

    def collect(self, *, read_only: bool = False, sections=SECTION_IDS) -> dict:
        import playwright

        package = Path(playwright.__file__).parent
        node = package / 'driver' / ('node.exe' if os.name == 'nt' else 'node')
        helper = Path(__file__).parent.parent / 'browser' / 'rts_sections.cjs'
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        log_path = self.base_dir / 'logs' / 'rts_sections.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open('a', encoding='utf-8') as log:
            log.write(f'[{datetime.now().isoformat(timespec="seconds")}] sections={",".join(sections)}; read_only={read_only}\n')
            log.flush()
            result = subprocess.run(
                [str(node), str(helper)], input=json.dumps({'read_only': read_only, 'sections': sections}),
                stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8', timeout=2000,
                creationflags=flags, check=False,
            )
        if result.returncode:
            raise RuntimeError('local RTS CDP helper failed; traversal is not confirmed')
        return json.loads(result.stdout)


class RtsSectionsSource:
    def __init__(self, base_dir: Path, *, client=None, now: datetime | None = None, read_only: bool = False, section_ids=SECTION_IDS):
        self.base_dir = base_dir
        self.client = client or RtsSectionsClient(base_dir)
        self.now = now
        self.read_only = read_only
        if not section_ids or any(s not in SECTION_IDS for s in section_ids):
            raise ValueError('Unknown or empty RTS section selection')
        self.section_ids = tuple(section_ids)

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return self.fetch_with_report(keywords).tenders

    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        try:
            with _collection_lock(self.base_dir):
                return self._fetch_locked()
        except RtsCollectionBusy:
            # Do not touch the snapshot while its owner is reading/updating it.
            return SourceFetchResult(health=[
                SourceHealth(s, 'skipped', 0, 0.0, 'another RTS collection is active; duplicate skipped')
                for s in self.section_ids
            ])

    def _fetch_locked(self) -> SourceFetchResult:
        # Deliberately ignore keyword selection: use only regional listing filters.
        started = monotonic()
        now = self.now or datetime.now()
        path = self.base_dir / 'data' / 'rts_sections_state.json'
        try:
            previous = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {}
        cache = previous.get('sections', {}) if isinstance(previous, dict) else {}
        if not isinstance(cache, dict):
            cache = {}
        try:
            payload = self.client.collect(read_only=self.read_only, sections=self.section_ids)
            sections = {s['source']: s for s in payload['sections'] if s.get('source') in SECTION_IDS}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
            sections = {s: {'reason': f'browser unavailable: {type(exc).__name__}'} for s in self.section_ids}
        result = SourceFetchResult()
        state = dict(cache)
        fresh = []
        # Freshness describes completed observations, not the start of a long traversal.
        now = self.now or datetime.now()
        for source in self.section_ids:
            section = sections.get(source, {'reason': 'section was not returned by browser collector'})
            parsed = []
            parse_failed = False
            for page in section.get('pages', []):
                recognized = _parse_section_rows(source, page['html'], page['url'], now)
                items = _regional_rows(source,page['url'],recognized)
                # Pages from other regions may have no locally relevant rows; not a parser error.
                if not recognized:
                    parse_failed = True
                parsed.extend(items)
            rows = list({t.unique_key: t for t in parsed}.values())
            detail = section.get('reason', '')
            complete = bool(section.get('complete')) and not parse_failed
            if parse_failed:
                detail = 'listing rows present but parser did not recognize them; previous snapshot preserved'
            old = cache.get(source, {})
            if not isinstance(old, dict):
                old = {}
            last_complete = old.get('last_complete_at')
            if complete:
                last_complete = now.isoformat(timespec='seconds')
            current_count = len(rows)
            if complete or rows:
                fresh.extend(rows)
                state[source] = {
                    'captured_at': now.isoformat(timespec='seconds'), 'last_complete_at': last_complete,
                    'complete': complete, 'items': _merge_snapshot(old, rows, now.isoformat(timespec='seconds'), complete=complete),
                }
                status = 'ok' if complete and rows else ('empty' if complete else 'partial')
                rows = _cached_rows(state[source], now)
                if not complete:
                    detail += f'; observed_now={current_count}; fresh cached/observed rows={len(rows)}; older pages preserved without renewing their timestamps'
            else:
                status = ('blocked' if any(x in detail for x in ('blocked', 'login')) else
                          'error' if 'server error' in detail else 'skipped')
                rows = _cached_rows(old, now)
                if rows:
                    detail += '; cached rows with original per-row timestamps (not a fresh collection)'
                elif old.get('items'):
                    detail += '; cached rows too old or invalid, not imported'
            detail = (f'checked={now.isoformat(timespec="seconds")}; pages={len(section.get("pages", []))}; '
                      f'last_complete={last_complete or "never"}; ' + (detail or 'regional listing fully traversed'))
            result.tenders.extend(rows)
            result.health.append(SourceHealth(source, status, len(rows), round(monotonic()-started,3), detail))
            if status in {'blocked', 'skipped', 'error'}:
                result.errors.append(f'{source}: {detail}')
            state.setdefault(source, old)
            state[source].update(last_checked_at=now.isoformat(timespec='seconds'), status=status, detail=detail)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({'sections': state}, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
        if fresh:
            RtsAccumulator(self.base_dir / 'data' / 'tenders.db').add_many(fresh)
        return result
