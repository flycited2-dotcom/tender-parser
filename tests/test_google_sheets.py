from datetime import datetime

from tender_parser.google_sheets import GoogleSheetsConfig, GoogleSheetsRegistry
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
    )

    assert result.status == "synced"
    values_payload = next(payload for url, payload in session.posts if url.endswith("values:batchUpdate"))
    ranges = {item["range"]: item["values"] for item in values_payload["data"]}
    active = ranges["'Все актуальные'!A2:T2"][0]
    assert active[16:18] == ["Беру", "Позвонить"]
    assert active[8] == '=IF(H2="","",INT(H2-TODAY()))'
    archive = ranges["'Архив'!A2:T2"][0]
    assert archive[0] == "fake:closed"
    assert archive[2] == "Не найдена в последнем запуске"
    selected = ranges["'Мой отбор'!A2:T2"][0]
    assert selected[0] == "fake:1"
    table_payload = session.posts[-1][1]
    assert table_payload["requests"][0]["updateTable"]["table"]["range"]["endRowIndex"] == 2
