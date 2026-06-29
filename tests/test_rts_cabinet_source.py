from __future__ import annotations

from pathlib import Path

from tender_parser.browser.rts_cabinet import RtsCabinetBrowserClient
from tender_parser.browser.session import check_chrome_debug_endpoint
from tender_parser.sources.rts_cabinet import (
    RtsCabinetBrowserSource,
    SourceFetchError,
    detect_cabinet_state,
    parse_cabinet_page,
)


FIXTURES = Path("tests/fixtures")


class OkResponse:
    ok = True


def test_check_chrome_debug_endpoint_returns_true_for_local_debug_server(monkeypatch) -> None:
    def fake_get(url: str, timeout: int) -> OkResponse:
        assert url == "http://127.0.0.1:9222/json/version"
        assert timeout == 2
        return OkResponse()

    monkeypatch.setattr("tender_parser.browser.session.requests.get", fake_get)

    assert check_chrome_debug_endpoint("http://127.0.0.1:9222") is True


class FakePage:
    url = "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx"

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        assert state == "domcontentloaded"
        assert timeout == 5000

    def content(self) -> str:
        return "<html><body>ok</body></html>"


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts = [type("Context", (), {"pages": [FakePage()]})()]
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    def connect_over_cdp(self, debug_url: str) -> FakeBrowser:
        assert debug_url == "http://127.0.0.1:9222"
        return self.browser


class FakePlaywrightManager:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)

    def __enter__(self) -> "FakePlaywrightManager":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def test_browser_client_does_not_close_external_chrome() -> None:
    browser = FakeBrowser()
    client = RtsCabinetBrowserClient(playwright_factory=lambda: FakePlaywrightManager(browser))

    url, html = client.read_current_page()

    assert "rts-tender.ru" in url
    assert "ok" in html
    assert browser.closed is False


def test_parse_cabinet_page_extracts_visible_results() -> None:
    html = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")

    tenders = parse_cabinet_page(html, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx")

    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.source == "rts-cabinet"
    assert tender.tender_number == "RTS-4455001"
    assert tender.title == "Поставка МФУ в Республику Крым"
    assert tender.url == "https://223.rts-tender.ru/trade/view/?id=4455001"
    assert tender.customer == "ГБУ РК Тест"
    assert tender.region == "Республика Крым"
    assert tender.price == 45_000.0
    assert tender.deadline is not None
    assert tender.deadline.year == 2026
    assert tender.status == "Прием заявок"
    assert tender.detail_status == "enriched"
    assert tender.source_confidence == 0.9


def test_detect_cabinet_state_identifies_results_login_and_blocked() -> None:
    results = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    login = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    blocked = (FIXTURES / "rts_cabinet_blocked_sample.html").read_text(encoding="utf-8")

    assert detect_cabinet_state(results, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx") == "results"
    assert detect_cabinet_state(login, "https://223.rts-tender.ru/login") == "login"
    assert detect_cabinet_state(blocked, "https://223.rts-tender.ru/captcha") == "blocked"


class FakeCabinetClient:
    def __init__(self, html: str, url: str = "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx") -> None:
        self.html = html
        self.url = url

    def read_current_page(self) -> tuple[str, str]:
        return self.url, self.html


def test_cabinet_source_returns_health_for_visible_results() -> None:
    html = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    source = RtsCabinetBrowserSource(client=FakeCabinetClient(html))

    result = source.fetch_with_report(["мфу"])

    assert len(result.tenders) == 1
    assert result.health[0].source == "rts-cabinet"
    assert result.health[0].status == "ok"
    assert result.health[0].found == 1


def test_cabinet_source_reports_login_page_as_blocked() -> None:
    html = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    source = RtsCabinetBrowserSource(client=FakeCabinetClient(html, "https://223.rts-tender.ru/login"))

    result = source.fetch_with_report(["мфу"])

    assert result.tenders == []
    assert result.health[0].status == "blocked"
    assert "login required" in result.health[0].detail
    assert result.errors


def test_cabinet_fetch_keywords_raises_when_not_authenticated() -> None:
    html = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    source = RtsCabinetBrowserSource(client=FakeCabinetClient(html, "https://223.rts-tender.ru/login"))

    try:
        source.fetch_keywords(["мфу"])
    except SourceFetchError as exc:
        assert "login required" in str(exc)
    else:
        raise AssertionError("expected SourceFetchError")
