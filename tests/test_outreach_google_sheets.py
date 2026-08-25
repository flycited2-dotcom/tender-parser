from tender_parser.customers import CUSTOMER_HEADERS
from tender_parser.outreach_google_sheets import (
    GoogleOutreachSourceConfig,
    GoogleSheetsOutreachReader,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class ReadOnlySession:
    def __init__(self):
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if "/values/" in url:
            if "customers" in url:
                return FakeResponse(
                    {
                        "values": [
                            CUSTOMER_HEADERS,
                            [
                                "org",
                                "Организация",
                                "Бюджетное учреждение",
                                "Республика Крым",
                                "9100000000",
                                "",
                                "",
                                "purchase@centre.ru",
                                "",
                                "",
                                "",
                                "ЕИС",
                                "https://example.test/tender",
                                "24.08.2026",
                                "Готов к обращению",
                                "",
                            ],
                        ]
                    }
                )
            return FakeResponse(
                {
                    "values": [
                        ["Email", "Название", "Тип", "Город", "Телефон", "Сайт", "Соцсети", "Адрес", "Источник", "Статус", "Дата_отправки", "Этап", "Заметка"],
                        ["old@hotel.ru", "Отель", "отель", "Ялта", "", "", "", "", "", "отправлено", "2026-07-20", "", ""],
                    ]
                }
            )
        sheet_name = "Потенциальные заказчики" if "customers" in url else "Рассылка"
        row_count = 1000 if "customers" in url else 1399
        return FakeResponse(
            {
                "sheets": [
                    {
                        "properties": {
                            "title": sheet_name,
                            "gridProperties": {"rowCount": row_count},
                        }
                    }
                ]
            }
        )


def test_reader_uses_metadata_derived_ranges_and_only_get_requests() -> None:
    session = ReadOnlySession()
    reader = GoogleSheetsOutreachReader(
        GoogleOutreachSourceConfig(
            customer_spreadsheet_id="customers",
            horeca_spreadsheet_id="horeca",
            service_account_file=None,
        ),
        session=session,
    )

    snapshot = reader.read()

    assert len(snapshot.customer_rows) == 1
    assert len(snapshot.horeca_rows) == 1
    urls = [url for url, _ in session.gets]
    assert any("A1%3AP1000" in url for url in urls)
    assert any("A1%3AM1399" in url for url in urls)
    assert len(session.gets) == 4

