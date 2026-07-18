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


def test_parse_cabinet_page_extracts_jqgrid_results() -> None:
    html = """
    <html>
      <body>
        <table class="ui-jqgrid-htable">
          <tr>
            <th>\u041d\u043e\u043c\u0435\u0440 \u0437\u0430\u043a\u0443\u043f\u043a\u0438</th>
            <th>\u041d\u0430\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435 \u0437\u0430\u043a\u0443\u043f\u043a\u0438</th>
            <th>\u041d\u0430\u0447\u0430\u043b\u044c\u043d\u0430\u044f \u0446\u0435\u043d\u0430</th>
          </tr>
        </table>
        <table id="BaseMainContent_MainContent_jqgTrade">
          <tr class="jqgrow" id="5967801">
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_PublicationDate">30.06.2026 17:12<br>\u041c\u0421\u041a</td>
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_Number">
              <a href="/supplier/auction/Trade/View.aspx?Id=3952750&amp;Logging=TradeByNumber">RTS454-26043531702296</a>
            </td>
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_OrganizerName">\u0423\u0424\u041f\u0421 \u0413.\u041c\u041e\u0421\u041a\u0412\u042b</td>
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_Region">\u0433. \u041c\u043e\u0441\u043a\u0432\u0430</td>
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_TradeName">
              <a href="/supplier/auction/Trade/View.aspx?Id=3952750&amp;Logging=TradeByName">
                \u041e\u043a\u0430\u0437\u0430\u043d\u0438\u0435 \u0443\u0441\u043b\u0443\u0433 \u043f\u043e \u0444\u0438\u0437\u0438\u0447\u0435\u0441\u043a\u043e\u0439 \u043e\u0445\u0440\u0430\u043d\u0435
              </a>
            </td>
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_Name">
              \u041e\u043a\u0430\u0437\u0430\u043d\u0438\u0435 \u0443\u0441\u043b\u0443\u0433 \u043f\u043e \u0444\u0438\u0437\u0438\u0447\u0435\u0441\u043a\u043e\u0439 \u043e\u0445\u0440\u0430\u043d\u0435
            </td>
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_StartPrice">184 773,66 \u0440\u0443\u0431.</td>
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_ApplicationEndDate">08.07.2026 09:00<br>\u041c\u0421\u041a</td>
            <td aria-describedby="BaseMainContent_MainContent_jqgTrade_LotStateString">\u041f\u0440\u0438\u0435\u043c \u0437\u0430\u044f\u0432\u043e\u043a</td>
          </tr>
        </table>
      </body>
    </html>
    """

    tenders = parse_cabinet_page(html, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx")

    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.tender_number == "RTS454-26043531702296"
    assert tender.title == "\u041e\u043a\u0430\u0437\u0430\u043d\u0438\u0435 \u0443\u0441\u043b\u0443\u0433 \u043f\u043e \u0444\u0438\u0437\u0438\u0447\u0435\u0441\u043a\u043e\u0439 \u043e\u0445\u0440\u0430\u043d\u0435"
    assert tender.url == "https://223.rts-tender.ru/supplier/auction/Trade/View.aspx?Id=3952750&Logging=TradeByName"
    assert tender.customer == "\u0423\u0424\u041f\u0421 \u0413.\u041c\u041e\u0421\u041a\u0412\u042b"
    assert tender.region == "\u0433. \u041c\u043e\u0441\u043a\u0432\u0430"
    assert tender.price == 184_773.66
    assert tender.deadline is not None
    assert tender.deadline.year == 2026
    assert tender.status == "\u041f\u0440\u0438\u0435\u043c \u0437\u0430\u044f\u0432\u043e\u043a"


def test_detect_cabinet_state_results_beat_captcha_word_in_content() -> None:
    from tender_parser.sources.rts_cabinet import detect_cabinet_state

    results = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    with_word = results.replace(
        "Оказание услуг по физической охране",
        "Проверка безопасности зданий и сооружений (captcha)",
    )

    state = detect_cabinet_state(with_word, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx")

    assert state == "results"


def test_detect_cabinet_state_vyhod_needs_word_boundary() -> None:
    from tender_parser.sources.rts_cabinet import detect_cabinet_state

    login_html = """
    <html><body>
      <form><input type="password" name="pw"></form>
      <footer>Поддержка работает без выходных</footer>
    </body></html>
    """

    state = detect_cabinet_state(login_html, "https://www.rts-tender.ru/account")

    assert state == "login"


def test_parse_cabinet_page_skips_rows_without_number_and_link() -> None:
    from tender_parser.sources.rts_cabinet import parse_cabinet_page

    html = """
    <html><body>
      <table>
        <tr><th>Номер</th><th>Наименование</th><th>Заказчик</th></tr>
        <tbody>
          <tr><td>04.08.2026 10:00</td><td>строка виджета без номера и ссылки</td><td>АО Василек</td></tr>
        </tbody>
      </table>
    </body></html>
    """

    tenders = parse_cabinet_page(html, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx")

    assert tenders == []


def test_detect_cabinet_state_identifies_results_login_and_blocked() -> None:
    results = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    login = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    blocked = (FIXTURES / "rts_cabinet_blocked_sample.html").read_text(encoding="utf-8")

    assert detect_cabinet_state(results, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx") == "results"
    assert detect_cabinet_state(login, "https://223.rts-tender.ru/login") == "login"
    assert detect_cabinet_state(blocked, "https://223.rts-tender.ru/captcha") == "blocked"


def test_detect_cabinet_state_keeps_logged_in_search_page_unknown() -> None:
    html = """
    <html>
      <head><title>\u041f\u043e\u0438\u0441\u043a \u0437\u0430\u043a\u0443\u043f\u043e\u043a</title></head>
      <body>
        <a>\u0412\u044b\u0445\u043e\u0434</a>
        <p>\u0415\u0441\u043b\u0438 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u043d\u0435 \u0431\u0443\u0434\u0435\u0442 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e,
        \u043f\u043e\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u044b\u0439 \u0432\u0445\u043e\u0434 \u0432 \u043b\u0438\u0447\u043d\u044b\u0439
        \u043a\u0430\u0431\u0438\u043d\u0435\u0442.</p>
      </body>
    </html>
    """

    state = detect_cabinet_state(html, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx")

    assert state == "unknown"


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
