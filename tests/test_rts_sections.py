import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tender_parser.cli import build_source_for_profile
from tender_parser.filters import evaluate_tender
from tender_parser.sources.rts_market_modern import parse_market_deadline, parse_modern_market_page
from tender_parser.sources.rts_sections import RtsSectionsClient, RtsSectionsSource, SECTION_IDS, parse_section
from tender_parser.sources.rts_cabinet import parse_cabinet_page


NOW = datetime(2026, 10, 30, 19, 0)
URL = 'https://market.rts-tender.ru/search/sell?regs=9100000000000'
CARD = '''<article class="catalog__strip">
<a href="https://market.rts-tender.ru/search/sell/10817362/request"><span rts-at="requestCardTileTitle">Поставка геотекстиля нетканого</span></a>
<p rts-at="requestCardRegionNameText">Регионы поставки: Респ. Крым</p>
<div rts-at="requestCardCustomerName">Бюджетное учреждение</div>
<span rts-at="requestCardTradePrice">100 000,00 ₽</span>
<span rts-at="requestCardAppEndDate">до 02 ноября, 13:00 МСК</span>
<p rts-at="requestCardAppStateIndicatorText">Прием предложений</p>
<span>223-ФЗ</span></article>'''


class FakeClient:
    def __init__(self, sections):
        self.sections = sections

    def collect(self, **kwargs):
        return {'sections': self.sections}


def test_modern_market_extracts_direct_link_number_price_deadline():
    [t] = parse_modern_market_page(CARD, URL, now=NOW)
    assert t.source == 'rts-market-modern'
    assert t.tender_number == '10817362'
    assert t.url.endswith('/10817362/request')
    assert t.platform_url == t.url
    assert t.deadline == datetime(2026, 11, 2, 13, 0)
    assert t.price == 100_000
    assert t.customer == 'Бюджетное учреждение'
    assert t.procurement_law == '223-ФЗ'


@pytest.mark.parametrize(('value', 'now', 'expected'), [
    ('до 01 января, 10:00 МСК', datetime(2026,12,29), datetime(2027,1,1,10)),
    ('до 29 декабря, 10:00', datetime(2027,1,2), datetime(2026,12,29,10)),
    ('до 01 ноября 2027, 10:00', NOW, datetime(2027,11,1,10)),
    ('02.11.2026 13:00', NOW, datetime(2026,11,2,13)),
    ('31 февраля, 10:00', NOW, None), ('нет срока', NOW, None),
])
def test_deadlines(value, now, expected):
    assert parse_market_deadline(value, now) == expected


def test_market_dedupes_and_rejects_external_links():
    assert len(parse_modern_market_page(CARD + CARD, URL, now=NOW)) == 1
    assert not parse_modern_market_page(CARD.replace('https://market.rts-tender.ru/search/sell/10817362', 'https://example.test/search/sell/10817362'), URL)


@pytest.mark.parametrize('title', ['Препарат медицинский', 'Поставка кроватей', 'Ремонт кондиционеров', 'Поставка геотекстиля нетканого', 'Оказание юридических услуг'])
def test_all_topics_are_collected_before_excel_filtering(title):
    html = CARD.replace('Поставка геотекстиля нетканого', title)
    [t] = parse_section('rts-market-modern', html, URL, NOW)
    assert t.title == title
    # A drug may be excluded later, but it is still a collected record.
    if title == 'Препарат медицинский':
        assert evaluate_tender(t, now=NOW).filter_status == 'excluded'


def test_only_requested_regions_are_kept():
    assert not parse_section('rts-market-modern', CARD.replace('Респ. Крым', 'Московская область'), URL, NOW)


def test_region_filtered_market_keeps_cards_without_printed_region_for_review():
    html = CARD.replace('Респ. Крым', '')
    [record] = parse_section('rts-market-modern', html, URL, NOW)
    assert record.region is None
    assert evaluate_tender(record, now=NOW).review_priority == 'excluded'


def test_app_cabinet_string_fields_and_number_not_preference_badges():
    html = '''<table><tr class="jqgrow">
      <td aria-describedby="jqgTrades_NotificationNumber"><a href="/supplier/auction/Trade/View.aspx?Id=abc">0875100000126000012</a>Нац. режим СМП</td>
      <td aria-describedby="jqgTrades_TradeName"><a href="/supplier/auction/Trade/View.aspx?Id=abc">Поставка мебели</a></td>
      <td aria-describedby="jqgTrades_Region">Респ. Крым</td>
      <td aria-describedby="jqgTrades_StartPriceString">123 000,00 Р</td>
      <td aria-describedby="jqgTrades_ApplicationEndDateString">02.11.2026 10:00 (по московскому времени)</td>
      <td aria-describedby="jqgTrades_PublicationDateString">30.10.2026 18:00 (по московскому времени)</td>
    </tr></table>'''
    [t] = parse_section('rts-44', html, 'https://app.rts-tender.ru/supplier/auction/Trade/Search.aspx', NOW)
    assert t.source == 'rts-44'
    assert t.tender_number == '0875100000126000012'
    assert t.official_number == '0875100000126000012'
    assert t.price == 123000
    assert t.deadline == datetime(2026,11,2,10)
    assert t.published_at == datetime(2026,10,30,18)
    assert t.url.endswith('View.aspx?Id=abc')


def test_all_four_sources_are_part_of_daily_fast_and_full_profiles(tmp_path):
    for profile in ('fast', 'full'):
        composite = build_source_for_profile(profile, base_dir=tmp_path)
        sources = composite.sources[0].sources
        [rts] = [s for s in sources if isinstance(s, RtsSectionsSource)]
        assert rts.base_dir == tmp_path


def successful_section():
    return {'source':'rts-market-modern', 'complete':True, 'pages':[{'html':CARD,'url':URL}]}


def test_health_and_snapshot_are_separate_for_four_sections(tmp_path):
    client = FakeClient([successful_section(), {'source':'rts-223','reason':'login: sign in manually'}])
    result = RtsSectionsSource(tmp_path, client=client, now=NOW).fetch_with_report(['unrelated keyword'])
    assert len(result.tenders) == 1
    assert [h.source for h in result.health] == list(SECTION_IDS)
    assert [h.status for h in result.health] == ['ok', 'blocked', 'skipped', 'skipped']
    state = json.loads((tmp_path/'data/rts_sections_state.json').read_text(encoding='utf8'))['sections']
    assert state['rts-market-modern']['last_complete_at'] == NOW.isoformat()
    assert 'last_complete_at' not in state['rts-223']


def test_blocked_refresh_does_not_renew_cache_freshness(tmp_path):
    RtsSectionsSource(tmp_path, client=FakeClient([successful_section()]), now=NOW).fetch_with_report([])
    blocked = FakeClient([{'source':'rts-market-modern','reason':'blocked: manual browser check required'}])
    result = RtsSectionsSource(tmp_path, client=blocked, now=NOW+timedelta(hours=1)).fetch_with_report([])
    assert result.health[0].status == 'blocked'
    assert 'not a fresh collection' in result.health[0].detail
    assert len(result.tenders) == 1
    state = json.loads((tmp_path/'data/rts_sections_state.json').read_text(encoding='utf8'))['sections']
    assert state['rts-market-modern']['captured_at'] == NOW.isoformat()
    assert state['rts-market-modern']['last_complete_at'] == NOW.isoformat()
    result = RtsSectionsSource(tmp_path, client=blocked, now=NOW+timedelta(hours=31)).fetch_with_report([])
    assert not result.tenders


def test_partial_is_not_reported_as_complete_and_keeps_all_collected_topics(tmp_path):
    section = successful_section()
    section.update(complete=False, reason='page budget reached')
    result = RtsSectionsSource(tmp_path, client=FakeClient([section]), now=NOW).fetch_with_report([])
    assert len(result.tenders) == 1
    assert result.health[0].status == 'partial'
    assert 'last_complete=never' in result.health[0].detail


def test_confirmed_empty_can_be_successful_but_unknown_is_not(tmp_path):
    result = RtsSectionsSource(tmp_path, client=FakeClient([
        {'source':'rts-market-modern','complete':True,'pages':[]},
        {'source':'rts-223','complete':False,'reason':'unknown listing DOM'},
    ]), now=NOW).fetch_with_report([])
    assert result.health[0].status == 'empty'
    assert result.health[1].status == 'skipped'


def test_off_topic_rows_survive_in_excel_excluded_sheet(tmp_path):
    from openpyxl import load_workbook
    from tender_parser.exporters.excel import export_excel

    html = CARD.replace('Поставка геотекстиля нетканого', 'Поставка лекарственных препаратов')
    [raw] = parse_section('rts-market-modern', html, URL, NOW)
    evaluated = evaluate_tender(raw, now=NOW)
    assert evaluated.review_priority == 'excluded'
    path = export_excel([], [], [], [evaluated], tmp_path/'report.xlsx', regional_tenders=[evaluated], now=NOW)
    workbook = load_workbook(path)
    excluded = workbook['Отсеянные']
    assert any(evaluated.title == cell.value for row in excluded for cell in row)
    assert any(cell.hyperlink and cell.hyperlink.target == raw.url for row in excluded for cell in row)
    assert any(raw.title == cell.value for row in workbook['Все региональные'] for cell in row)
    workbook.close()


def test_targeted_refresh_preserves_other_sections_status_and_freshness(tmp_path):
    RtsSectionsSource(tmp_path, client=FakeClient([successful_section()]), now=NOW).fetch_with_report([])
    result = RtsSectionsSource(tmp_path, client=FakeClient([
        {'source':'rts-223','complete':True,'pages':[]},
    ]), now=NOW+timedelta(hours=1), section_ids=('rts-223',)).fetch_with_report([])
    assert [h.source for h in result.health] == ['rts-223']
    state = json.loads((tmp_path/'data/rts_sections_state.json').read_text(encoding='utf8'))['sections']
    assert state['rts-market-modern']['status'] == 'ok'
    assert state['rts-market-modern']['last_complete_at'] == NOW.isoformat()


def test_local_client_runs_hidden_without_shell_and_logs_progress(tmp_path, monkeypatch):
    import os
    import subprocess
    from types import SimpleNamespace

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        kwargs['stderr'].write('rts-poisk: captured 5 pages\n')
        return SimpleNamespace(returncode=0, stdout='{"sections": []}')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert RtsSectionsClient(tmp_path).collect(sections=('rts-poisk',)) == {'sections': []}
    [(_, kwargs)] = calls
    assert kwargs['creationflags'] == (subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert not kwargs.get('shell')
    assert json.loads(kwargs['input'])['sections'] == ['rts-poisk']
    assert 'captured 5 pages' in (tmp_path/'logs/rts_sections.log').read_text(encoding='utf8')


def test_browser_timeout_is_not_a_success_and_does_not_destroy_snapshot(tmp_path):
    import subprocess

    class TimeoutClient:
        def collect(self, **kwargs):
            raise subprocess.TimeoutExpired('local node helper', 1)

    RtsSectionsSource(tmp_path, client=FakeClient([successful_section()]), now=NOW).fetch_with_report([])
    result = RtsSectionsSource(tmp_path, client=TimeoutClient(), now=NOW+timedelta(hours=1)).fetch_with_report([])
    assert all(h.status == 'skipped' for h in result.health)
    assert len(result.tenders) == 1
    state = json.loads((tmp_path/'data/rts_sections_state.json').read_text(encoding='utf8'))['sections']
    assert state['rts-market-modern']['last_complete_at'] == NOW.isoformat()


def test_partial_pass_keeps_unseen_rows_without_extending_their_freshness(tmp_path):
    RtsSectionsSource(tmp_path, client=FakeClient([successful_section()]), now=NOW).fetch_with_report([])
    new_section = successful_section()
    new_section['pages'] = [{'url': URL, 'html': CARD.replace('10817362', '99999999')}]
    new_section.update(complete=False, reason='page budget reached')
    result = RtsSectionsSource(tmp_path, client=FakeClient([new_section]), now=NOW+timedelta(hours=20)).fetch_with_report([])
    assert {t.tender_number for t in result.tenders} == {'10817362', '99999999'}
    state = json.loads((tmp_path/'data/rts_sections_state.json').read_text(encoding='utf8'))['sections']['rts-market-modern']
    observed = {obj['tender_number']: obj['observed_at'] for obj in state['items']}
    assert observed['10817362'] == NOW.isoformat()
    assert observed['99999999'] == (NOW+timedelta(hours=20)).isoformat()
    failed = FakeClient([{'source':'rts-market-modern','reason':'blocked'}])
    result = RtsSectionsSource(tmp_path, client=failed, now=NOW+timedelta(hours=31)).fetch_with_report([])
    assert [t.tender_number for t in result.tenders] == ['99999999']
    assert 'not a fresh collection' in result.health[0].detail


def test_duplicate_collection_does_not_read_browser_or_modify_snapshot(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import tender_parser.sources.rts_sections as sections_module

    class UnexpectedClient:
        def collect(self, **kwargs):
            pytest.fail('Duplicate run must not touch the browser')

    @contextmanager
    def busy_lock(_):
        raise sections_module.RtsCollectionBusy()
        yield

    path = tmp_path/'data/rts_sections_state.json'
    path.parent.mkdir()
    path.write_text('{"sections": {"preserved": 1}}', encoding='utf8')
    before = path.read_bytes()
    monkeypatch.setattr(sections_module, '_collection_lock', busy_lock)
    result = RtsSectionsSource(tmp_path, client=UnexpectedClient(), now=NOW).fetch_with_report([])
    assert all(h.status == 'skipped' for h in result.health)
    assert path.read_bytes() == before
