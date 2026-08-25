from datetime import datetime

from tender_parser.google_sheets import (
    DATA_HEADERS,
    GoogleSheetsConfig,
    GoogleSheetsRegistry,
    LEGACY_DATA_HEADERS,
    _migrate_existing_row,
    _chunked_value_updates,
    _record_row,
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
                            "properties": {
                                "sheetId": 1,
                                "title": "Все актуальные",
                                "gridProperties": {"columnCount": 20},
                            },
                            "tables": [{"tableId": "active", "name": "ActiveTendersTable"}],
                        }
                    ]
                }
            )
        if "A1%3AAC1" in url:
            return FakeResponse({"values": [LEGACY_DATA_HEADERS]})
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
        official_number="0174100000626000005",
        official_url=(
            "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html"
            "?regNumber=0174100000626000005"
        ),
        official_source="ЕИС",
        platform_number="AST-1",
        platform_url="https://utp.sberbank-ast.ru/purchase/1",
        procurement_law="44-ФЗ",
        resolution_method="rostender-meta",
        resolution_confidence=0.98,
    )


def test_disabled_registry_does_not_call_google() -> None:
    result = GoogleSheetsRegistry(GoogleSheetsConfig()).sync(
        [], [], SourceFetchResult(), generated_at=datetime(2026, 8, 8), profile="fast"
    )

    assert result.status == "disabled"


def test_large_sheet_is_split_into_bounded_row_ranges() -> None:
    rows = [[index] for index in range(401)]

    updates = _chunked_value_updates(
        "Все региональные", rows, last_column="AC", start_row=2
    )

    assert [item["range"] for item in updates] == [
        "'Все региональные'!A2:AC201",
        "'Все региональные'!A202:AC401",
        "'Все региональные'!A402:AC402",
    ]
    assert sum(len(item["values"]) for item in updates) == 401


def test_value_updates_are_posted_in_size_bounded_batches(monkeypatch) -> None:
    session = FakeSession()
    registry = GoogleSheetsRegistry(
        GoogleSheetsConfig(enabled=True, spreadsheet_id="sheet-1"),
        session=session,
    )
    monkeypatch.setattr("tender_parser.google_sheets.VALUE_BATCH_MAX_BYTES", 200)
    updates = [
        {"range": f"'Test'!A{index}:A{index}", "values": [["x" * 100]]}
        for index in range(1, 4)
    ]

    registry._post_value_batches(session, updates)

    posts = [payload for url, payload in session.posts if url.endswith("values:batchUpdate")]
    assert len(posts) == 3
    assert all(len(payload["data"]) == 1 for payload in posts)


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
        regional_tenders=[make_tender()],
    )

    assert result.status == "synced"
    expand_payload = next(
        payload
        for url, payload in session.posts
        if url.endswith(":batchUpdate")
        and any("appendDimension" in item for item in payload.get("requests", []))
    )
    assert expand_payload["requests"][0]["appendDimension"]["length"] == 9
    values_payload = next(payload for url, payload in session.posts if url.endswith("values:batchUpdate"))
    ranges = {item["range"]: item["values"] for item in values_payload["data"]}
    assert ranges["'Все региональные'!A1:AC1"][0] == DATA_HEADERS
    assert ranges["'Все региональные'!A2:AC2"][0][0] == "fake:1"
    assert ranges["'Все актуальные'!A1:AC1"][0] == DATA_HEADERS
    active = ranges["'Все актуальные'!A2:AC2"][0]
    assert active[16:18] == ["Беру", "Позвонить"]
    assert active[8] == '=IF(H2="";"";INT(H2-TODAY()))'
    assert active[12] == (
        '=HYPERLINK("https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html'
        '?regNumber=0174100000626000005";"0174100000626000005")'
    )
    assert "zakupki.gov.ru" in active[18]
    assert active[20] == '=HYPERLINK("https://example.test/1";"1")'
    assert "example.test/1" in active[21]
    assert active[22:28] == [
        "ЕИС",
        '=HYPERLINK("https://utp.sberbank-ast.ru/purchase/1";"AST-1")',
        '=HYPERLINK("https://utp.sberbank-ast.ru/purchase/1";"Открыть площадку")',
        "44-ФЗ",
        "rostender-meta",
        0.98,
    ]
    assert "documents.html" in active[28]
    archive = ranges["'Архив'!A2:AC2"][0]
    assert archive[0] == "fake:closed"
    assert archive[2] == "Не найдена в последнем запуске"
    selected = ranges["'Мой отбор'!A2:AC2"][0]
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
    active = ranges["'Все актуальные'!A2:AC2"][0]
    assert active[0] == "fake:1"
    assert active[2] == "⚠ Источник временно недоступен"
    assert "CAPTCHA" in active[19]
    archive = ranges["'Архив'!A2:AC2"][0]
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


def test_row_source_id_recovers_rts_cross_platform_search() -> None:
    assert _row_source_id(_source_link("rts-poisk")) == "rts-poisk"


def test_row_source_id_recovers_official_eis_xml_source() -> None:
    assert _row_source_id(_source_link("eis-regional-xml")) == "eis-regional-xml"


def test_customer_phone_is_written_as_literal_not_formula() -> None:
    row = ["org", "Организация", "", "", "", "", "", "mail@example.ru", "+7 978 000-00-00"]

    safe = _safe_customer_row(row)

    assert safe[8] == "'+7 978 000-00-00"


def test_generated_customer_hyperlink_remains_formula() -> None:
    formula = '=HYPERLINK("https://example.test/";"Открыть")'

    assert _safe_customer_row([formula]) == [formula]


def test_legacy_google_row_is_mapped_by_headers_without_moving_manual_fields() -> None:
    # The input order is deliberately shuffled to prove migration is driven by
    # names, not the old 20-column positions.
    headers = ["Комментарий", "Ключ", "Мой выбор", "Ссылка", "Номер", "Название"]
    row = [
        "Позвонить",
        "rostender:94216089",
        "Беру",
        "https://rostender.info/tender/94216089",
        "94216089",
        "Поставка МФУ",
    ]

    migrated = _migrate_existing_row(row, headers)

    assert migrated[0] == "rostender:94216089"
    assert migrated[4] == "Поставка МФУ"
    assert migrated[16:18] == ["Беру", "Позвонить"]
    assert migrated[12] == ""
    assert migrated[18] == ""
    assert migrated[20] == "94216089"  # original provenance is preserved
    assert migrated[21] == "https://rostender.info/tender/94216089"


def test_unresolved_rostender_row_does_not_claim_source_id_as_official() -> None:
    tender = TenderRecord(
        title="Поставка МФУ",
        url="https://rostender.info/region/krym/94216089-tender-postavka-mfu",
        source="rostender",
        tender_number="94216089",
        review_priority="review",
    )

    row = _record_row(tender, set(), {}, datetime(2026, 8, 15, 12, 0))

    assert row[12] == ""  # no confirmed official_number
    assert row[18] == ""  # source URL is not a direct official/platform URL
    assert row[20] == (
        '=HYPERLINK("https://rostender.info/region/krym/94216089-tender-postavka-mfu";'
        '"94216089")'
    )
    assert "rostender.info" in row[21]
