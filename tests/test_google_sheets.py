from datetime import datetime

from tender_parser.google_sheets import (
    GoogleSheetsConfig,
    GoogleSheetsRegistry,
    _row_source_id,
    _safe_customer_row,
    _source_link,
    _summary_rows,
)
from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth


class FakeResponse:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        if "/values/" not in url:
            return FakeResponse(
                {
                    "sheets": [
                        {
                            "properties": {"sheetId": 1, "title": "Все актуальные"},
                            "tables": [{"tableId": "active", "name": "ActiveTendersTable"}],
                        }
                    ]
                }
            )
        if "%D0%92%D1%81%D0%B5%20%D0%B0%D0%BA%D1%82%D1%83%D0%B0%D0%BB%D1%8C%D0%BD%D1%8B%D0%B5" in url:
            return FakeResponse(
                {
                    "values": [
                        [
                            "fake:1",
                            "",
                            "Актуальна",
                            "Горячий",
                            "Старое название",
                            "Крым",
                            100,
                            "10.08.2026 10:00",
                            2,
                            "Заказчик",
                            "Категория",
                            "fake",
                            "1",
                            "Подача заявок",
                            "07.08.2026 08:00",
                            "08.08.2026 08:00",
                            "Беру",
                            "Позвонить",
                            "https://example.test/1",
                            "совпадение",
                        ],
                        ["fake:closed", "", "Актуальна", "", "Закрытая"],
                    ]
                }
            )
        return FakeResponse({"values": []})

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.posts.append((url, kwargs.get("json") or {}))
        return FakeResponse({})


def make_tender() -> TenderRecord:
    return TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/1",
        source="fake",
        tender_number="1",
        region="Крым",
        deadline=datetime(2026, 8, 10, 10, 0),
        discovered_at=datetime(2026, 8, 7, 8, 0),
        review_priority="hot",
    )


def test_disabled_registry_does_not_call_google() -> None:
    result = GoogleSheetsRegistry(GoogleSheetsConfig()).sync(
        [], [], SourceFetchResult(), generated_at=datetime(2026, 8, 8), profile="fast"
    )

    assert result.status == "disabled"


def test_sync_preserves_selection_archives_missing_and_resizes_table() -> None:
    session = FakeSession()
    config = GoogleSheetsConfig(
        enabled=True,
        spreadsheet_id="sheet-1",
        spreadsheet_url="https://docs.google.com/spreadsheets/d/sheet-1/edit",
    )
    report = SourceFetchResult(
        tenders=[make_tender()],
        health=[SourceHealth("fake", "ok", 1, 0.1)],
    )

    result = GoogleSheetsRegistry(config, session=session).sync(
        [make_tender()],
        [make_tender()],
        report,
        generated_at=datetime(2026, 8, 8, 12, 0),
        profile="fast",
        raw_count=3,
        unique_count=2,
    )

    assert result.status == "synced"
    values_payload = next(payload for url, payload in session.posts if url.endswith("values:batchUpdate"))
    ranges = {item["range"]: item["values"] for item in values_payload["data"]}
    active = ranges["'Все актуальные'!A2:T2"][0]
    assert active[16:18] == ["Беру", "Позвонить"]
    assert active[8] == '=IF(H2="";"";INT(H2-TODAY()))'
    archive = ranges["'Архив'!A2:T2"][0]
    assert archive[0] == "fake:closed"
    assert archive[2] == "Не найдена в последнем запуске"
    selected = ranges["'Мой отбор'!A2:T2"][0]
    assert selected[0] == "fake:1"
    history = ranges["'История запусков'!A2:L2"][0]
    assert history[2:4] == [3, 2]
    table_payload = session.posts[-1][1]
    assert table_payload["requests"][0]["updateTable"]["table"]["range"]["endRowIndex"] == 2


def test_sync_keeps_last_good_rows_when_source_is_temporarily_blocked() -> None:
    session = FakeSession()
    config = GoogleSheetsConfig(
        enabled=True,
        spreadsheet_id="sheet-1",
        spreadsheet_url="https://docs.google.com/spreadsheets/d/sheet-1/edit",
    )
    report = SourceFetchResult(
        health=[SourceHealth("fake", "blocked", 0, 0.1, "CAPTCHA")],
    )

    result = GoogleSheetsRegistry(config, session=session).sync(
        [],
        [],
        report,
        generated_at=datetime(2026, 8, 9, 12, 0),
        profile="fast",
    )

    assert result.status == "synced"
    values_payload = next(
        payload for url, payload in session.posts if url.endswith("values:batchUpdate")
    )
    ranges = {item["range"]: item["values"] for item in values_payload["data"]}
    active = ranges["'Все актуальные'!A2:T2"][0]
    assert active[0] == "fake:1"
    assert active[2] == "⚠ Источник временно недоступен"
    assert "CAPTCHA" in active[19]
    archive = ranges["'Архив'!A2:T2"][0]
    assert archive[0] == "fake:closed"


def test_summary_uses_readable_label_for_missing_optional_import_folder() -> None:
    report = SourceFetchResult(
        health=[
            SourceHealth(
                "ImportFolderSource",
                "empty",
                0,
                0.0,
                r"folder missing: C:\private\workspace\imports",
            )
        ]
    )

    rows = _summary_rows(
        report,
        generated_at=datetime(2026, 8, 13),
        profile="fast",
        raw_count=0,
        unique_count=0,
        active_count=0,
        new_count=0,
    )

    assert rows[-1][1] == "Нет файлов"
    assert rows[-1][4] == "Папка imports не создана — ручных выгрузок нет"


def test_summary_links_official_crimea_small_purchases_source() -> None:
    report = SourceFetchResult(
        health=[SourceHealth("crimea-small-purchases", "ok", 41, 1.2)]
    )

    rows = _summary_rows(
        report,
        generated_at=datetime(2026, 8, 13),
        profile="fast",
        raw_count=41,
        unique_count=41,
        active_count=3,
        new_count=3,
    )

    assert rows[-1][0] == "Малые закупки Крыма"
    assert rows[-1][5] == '=HYPERLINK("https://zrk.rk.gov.ru/smallpurchases/";"Открыть")'


def test_intentionally_skipped_optional_source_does_not_mark_cycle_partial() -> None:
    report = SourceFetchResult(
        health=[
            SourceHealth(
                "sevastopol-small-purchases",
                "skipped",
                0,
                0.0,
                "ожидает устойчивого публичного API",
            )
        ]
    )

    rows = _summary_rows(
        report,
        generated_at=datetime(2026, 8, 13),
        profile="fast",
        raw_count=0,
        unique_count=0,
        active_count=0,
        new_count=0,
    )

    assert rows[1][5] == "Успешно"
    assert rows[-1][1] == "Пропущен"


def test_row_source_id_prefers_specific_rts_market_url_over_parent_site() -> None:
    assert _row_source_id(_source_link("rts-market")) == "rts-market"


def test_row_source_id_recovers_official_eis_xml_source() -> None:
    assert _row_source_id(_source_link("eis-regional-xml")) == "eis-regional-xml"


def test_customer_phone_is_written_as_literal_not_formula() -> None:
    row = ["org", "Организация", "", "", "", "", "", "mail@example.ru", "+7 978 000-00-00"]

    safe = _safe_customer_row(row)

    assert safe[8] == "'+7 978 000-00-00"


def test_generated_customer_hyperlink_remains_formula() -> None:
    formula = '=HYPERLINK("https://example.test/";"Открыть")'

    assert _safe_customer_row([formula]) == [formula]
