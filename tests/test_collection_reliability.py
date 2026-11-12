import json
from pathlib import Path
import pytest
import requests
from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.sources.composite import CompositeSource
from tender_parser.notifications import NotificationConfig, TelegramNotifier


@pytest.mark.parametrize('parallel', [False, True])
@pytest.mark.parametrize('failure', [MemoryError, ValueError])
def test_one_bad_source_keeps_other_results(parallel, failure):
    class Bad:
        def fetch_keywords(self, keywords):
            raise failure('secret URL must not be logged')
    class Good:
        def fetch_keywords(self, keywords):
            return [TenderRecord(title='Test', url='https://example.test/1', source='good')]
    result = CompositeSource([Bad(), Good()], parallel=parallel).fetch_with_report([])
    assert len(result.tenders) == 1
    assert result.health[0].status == 'error'
    assert failure.__name__ in result.health[0].detail
    assert 'secret' not in result.health[0].detail


def test_stream_limit_stops_before_decoding_and_closes_response():
    class Response:
        closed = False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield b'1234'
            yield b'5678'
            raise AssertionError('must stop reading')
        def close(self): self.closed = True
    response = Response()
    class Session:
        def get(self, url, **kwargs):
            assert kwargs['stream'] is True
            return response
    with pytest.raises(requests.RequestException, match='size limit'):
        get_with_retry(Session(), 'https://example.test', timeout=1, max_response_bytes=6)
    assert response.closed


class UploadSession:
    def __init__(self, failures, error=requests.ConnectTimeout):
        self.failures = failures
        self.error = error
        self.calls = 0
    def post(self, url, **kwargs):
        self.calls += 1
        assert kwargs['files']['document'][1].tell() == 0
        assert kwargs['timeout'] >= 180
        if self.calls <= self.failures:
            raise self.error('private-token')
        result = requests.Response()
        result.status_code = 200
        return result


def test_connect_timeout_retries_same_file_without_collecting(tmp_path, monkeypatch):
    monkeypatch.setattr('tender_parser.notifications.sleep', lambda _: None)
    path = tmp_path/'report.xlsx'
    path.write_bytes(b'report')
    session = UploadSession(1)
    result = TelegramNotifier(NotificationConfig(bot_token='test', chat_id='123'), session).send_document(path)
    assert result.status == 'sent'
    assert session.calls == 2


def test_failed_connect_is_durable_and_retry_requires_no_collection(tmp_path, monkeypatch):
    from tender_parser.delivery_retry import retry_pending
    monkeypatch.setattr('tender_parser.notifications.sleep', lambda _: None)
    config = NotificationConfig(bot_token='test', chat_id='123')
    monkeypatch.setattr(NotificationConfig, 'from_env', classmethod(lambda cls: config))
    path = tmp_path/'exports'/'report.xlsx'
    path.parent.mkdir()
    path.write_bytes(b'report')
    result = TelegramNotifier(config, UploadSession(3)).send_document(path)
    assert result.status == 'error'
    pending = list((path.parent/'.telegram_pending').glob('*.json'))
    assert len(pending) == 1
    assert 'bot_token' not in json.loads(pending[0].read_text())
    success = UploadSession(0)
    monkeypatch.setattr('tender_parser.notifications.requests.Session', lambda: success)
    assert retry_pending(tmp_path) == 0
    assert success.calls == 1
    assert not pending[0].exists()


def test_read_timeout_is_not_automatically_retried(tmp_path):
    path = tmp_path/'report.xlsx'
    path.write_bytes(b'report')
    session = UploadSession(3, error=requests.ReadTimeout)
    result = TelegramNotifier(NotificationConfig(bot_token='test', chat_id='123'), session).send_document(path)
    assert result.status == 'error'
    assert session.calls == 1
    assert not (tmp_path/'.telegram_pending').exists()
