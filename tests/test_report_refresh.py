from datetime import datetime, timedelta
from pathlib import Path
import json

from openpyxl import load_workbook

from tender_parser.models import TenderRecord
from tender_parser.storage import TenderStorage
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.exporters.json_exporter import export_run_report
from tender_parser.report_refresh import refresh_saved_report
from tender_parser.sources.rts_sections import _encode, load_saved_sections


NOW = datetime(2026,10,30,21,0)


def make_project(tmp_path: Path):
    storage=TenderStorage(tmp_path/'data/tenders.db')
    storage.upsert_many([TenderRecord(title='Поставка бумаги для принтера',source='eis-zakupki',
        tender_number='0875100000126000001',url='https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0875100000126000001',
        region='Республика Крым',deadline=NOW+timedelta(days=8),price=100000)])
    (tmp_path/'data/collection_result.json').write_text(json.dumps({'run_id':'original-daily'}),encoding='utf8')
    export_run_report(SourceFetchResult(health=[SourceHealth('eis-zakupki','ok',1,1.0,'')]),
        tmp_path/'exports/run_report.json',raw_count=1,unique_count=1,new_count=1,profile='fast')
    records=[TenderRecord(title='Поставка мебели',source='rts-market-modern',tender_number='123',
        url='https://market.rts-tender.ru/search/sell/123/request',region='Севастополь',deadline=NOW+timedelta(days=6),price=200000),
        TenderRecord(title='Поставка лекарственных препаратов',source='rts-market-modern',tender_number='124',
        url='https://market.rts-tender.ru/search/sell/124/request',region='Севастополь',deadline=NOW+timedelta(days=6),price=200000)]
    (tmp_path/'data/rts_sections_state.json').write_text(json.dumps({'sections':{'rts-market-modern':{
        'status':'ok','captured_at':NOW.isoformat(),'last_checked_at':NOW.isoformat(),
        'last_complete_at':NOW.isoformat(),'items':[_encode(t) for t in records]}}},ensure_ascii=False),encoding='utf8')


def test_cached_report_keeps_baseline_and_every_rts_topic_without_polling_sites(tmp_path,monkeypatch):
    make_project(tmp_path)
    import requests
    def no_network(*args,**kwargs):
        raise AssertionError('Rebuild must not poll platforms or publish unless requested')
    monkeypatch.setattr(requests.Session,'request',no_network)
    receipt=(tmp_path/'data/collection_result.json').read_bytes()
    result=refresh_saved_report(tmp_path,now=NOW,publish=False)
    assert result['raw_count']==3
    assert result['google_status']=='not_requested'
    assert result['telegram_status']=='not_requested'
    workbook=load_workbook(result['excel_path'])
    assert 'Потенциальные заказчики' in workbook.sheetnames
    assert 'Все региональные' in workbook.sheetnames
    assert any(c.value=='Поставка лекарственных препаратов' for row in workbook['Отсеянные'] for c in row)
    assert any(c.hyperlink and c.hyperlink.target=='https://market.rts-tender.ru/search/sell/124/request' for row in workbook['Отсеянные'] for c in row)
    workbook.close()
    report=json.loads((tmp_path/'exports/run_report.json').read_text(encoding='utf8'))
    assert report['profile']=='report-from-cache'
    assert 'not re-polled' in report['sources'][0]['detail']
    assert (tmp_path/'data/collection_result.json').read_bytes()==receipt
    assert Path(result['backup']).joinpath('run_report.json').exists()
    # A second rebuild must not lose the EIS baseline when RTS becomes the last DB batch.
    again=refresh_saved_report(tmp_path,now=NOW+timedelta(minutes=1),publish=False)
    assert again['raw_count']==3


def test_saved_sections_loader_is_read_only_and_rejects_old_rows(tmp_path):
    make_project(tmp_path)
    path=tmp_path/'data/rts_sections_state.json'
    before=path.read_bytes()
    assert len(load_saved_sections(tmp_path,now=NOW).tenders)==2
    old=load_saved_sections(tmp_path,now=NOW+timedelta(hours=31))
    assert not old.tenders
    assert old.health[0].status=='skipped'
    assert path.read_bytes()==before


def test_cached_report_preserves_regional_history_outside_latest_batch(tmp_path):
    make_project(tmp_path)
    import tender_parser.report_refresh as module
    storage = TenderStorage(tmp_path/'data/tenders.db')
    module._baseline(tmp_path, storage)  # Pin the daily batch before adding history.
    storage.upsert_many([TenderRecord(title='Историческая региональная закупка',
        source='eis-zakupki', tender_number='0875100000126000099',
        url='https://zakupki.gov.ru/test/99', region='Республика Крым',
        deadline=NOW-timedelta(days=20))])
    result = refresh_saved_report(tmp_path, now=NOW, publish=False)
    assert result['raw_count'] == 3  # History is not relabelled as a fresh collection.
    assert result['regional_count'] == 4
    workbook = load_workbook(result['excel_path'])
    assert any(c.value=='Историческая региональная закупка'
               for row in workbook['Все региональные'] for c in row)
    workbook.close()


def test_publish_uses_same_excel_and_regional_data_without_customer_network_fetches(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import tender_parser.report_refresh as module
    make_project(tmp_path)
    calls={}
    class FakeGoogle:
        def __init__(self,config):
            assert config.customer_max_per_run==0
        def sync(self,current,fresh,report,**kwargs):
            calls['regional']=kwargs['regional_tenders']
            calls['candidates']=kwargs['customer_candidates']
            return SimpleNamespace(status='synced')
    class FakeNotifier:
        def __init__(self,config):
            pass
        def send_document(self,path,**kwargs):
            calls['sent_path']=path
            calls['caption']=kwargs['caption']
            assert path.is_file()
            return SimpleNamespace(status='sent')
    monkeypatch.setattr(module,'GoogleSheetsRegistry',FakeGoogle)
    monkeypatch.setattr(module,'TelegramNotifier',FakeNotifier)
    result=refresh_saved_report(tmp_path,now=NOW,publish=True)
    assert result['google_status']=='synced'
    assert result['telegram_status']=='sent'
    assert calls['sent_path']==Path(result['excel_path'])
    assert len(calls['regional'])==3
    assert len(calls['candidates'])==3
    assert 'повторно не опрашивались' in calls['caption']


def test_report_rebuild_with_no_collection_receipt_can_be_repeated(tmp_path):
    make_project(tmp_path)
    (tmp_path/'data/collection_result.json').unlink()
    first=refresh_saved_report(tmp_path,now=NOW,publish=False)
    second=refresh_saved_report(tmp_path,now=NOW+timedelta(minutes=1),publish=False)
    assert first['raw_count']==second['raw_count']==3


def test_report_open_in_excel_is_preserved_and_new_copy_is_used(tmp_path,monkeypatch):
    make_project(tmp_path)
    old=tmp_path/'exports/tenders_2026-10-30.xlsx'
    old.write_bytes(b'old report held open')
    # Manual selections loader ignores an unreadable stand-in; do not parse it.
    import tender_parser.report_refresh as module
    monkeypatch.setattr(module,'load_manual_selections',lambda _: {})
    monkeypatch.setattr(module,'load_customer_rows',lambda _: [])
    original=Path.replace
    def held_open(source,target):
        if Path(target)==old:
            raise PermissionError('Workbook is open in Excel')
        return original(source,target)
    monkeypatch.setattr(Path,'replace',held_open)
    result=refresh_saved_report(tmp_path,now=NOW,publish=False)
    assert old.read_bytes()==b'old report held open'
    assert '_refresh_' in result['excel_path']
    assert Path(result['excel_path']).exists()
