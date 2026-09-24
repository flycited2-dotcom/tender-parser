"""Contract checks for the live Google Sheets projection."""

from pathlib import Path

from tender_parser.supplier_intelligence.sheet_schema import HEADERS
from tender_parser.supplier_intelligence.sheets_store import (
    SupplierSheetsStore, _category_filter_rows, _dashboard_rows, _email_formula, _equal,
)


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class _Session:
    def __init__(self, existing):
        self.existing = existing
        self.writes = []
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url))
        if url.endswith("/values:batchGet"):
            return _Response({"valueRanges": [
                {"values": self.existing[name]} for name in HEADERS
            ]})
        return _Response({"sheets": [
            {"properties": {"sheetId": index, "title": name,
                            "gridProperties": {"rowCount": 30, "columnCount": len(headers)}},
             "basicFilter": {"range": {"sheetId": index, "startRowIndex": 0,
                                       "endRowIndex": 30, "endColumnIndex": len(headers)}},
             "filterViews": [{"filterViewId": 99, "range": {"sheetId": index,
                 "startRowIndex": 0, "endRowIndex": 30,
                 "endColumnIndex": len(headers)}}] if name == "SUPPLIERS" else []}
            for index, (name, headers) in enumerate(HEADERS.items())
        ]})

    def post(self, url, **kwargs):
        self.calls.append(("post", url))
        self.writes.append((url, kwargs.get("json")))
        return _Response({})


def test_manual_supplier_review_status_survives_a_sync():
    supplier = {"SUPPLIER_ID": "SUP-000001", "REVIEW_STATUS": "NEW"}
    existing = {name: [headers] for name, headers in HEADERS.items()}
    existing["SUPPLIERS"].append([
        "SUP-000001" if header == "SUPPLIER_ID" else
        "CONFIRMED" if header == "REVIEW_STATUS" else ""
        for header in HEADERS["SUPPLIERS"]
    ])
    session = _Session(existing)
    changed = SupplierSheetsStore("sheet", Path("unused.json"), session=session).sync({
        "SUPPLIERS": [supplier],
    })
    assert changed["SUPPLIERS"] == 0
    assert all("'SUPPLIERS'!" not in part["range"]
               for url, body in session.writes if url.endswith("/values:batchUpdate")
               for part in body["data"])


def test_dashboard_uses_processed_message_and_current_error_counts():
    rows = _dashboard_rows({
        "DASHBOARD": [
            {"METRIC": "PROCESSED_MESSAGES", "VALUE": 17},
            {"METRIC": "ERRORS", "VALUE": 2},
        ],
        "INTERACTIONS": [
            {"GMAIL_ACCOUNT": "one@gmail.com", "MESSAGE_ID": "m1"},
            {"GMAIL_ACCOUNT": "one@gmail.com", "MESSAGE_ID": "m1"},
        ],
        "PROCESSING_LOG": [{"ERROR_COUNT": 9}],
    })
    counts = {row["METRIC"]: row["VALUE"] for row in rows}
    assert counts["Всего обработанных писем"] == 17
    assert counts["Ошибки обработки"] == 2


def test_dashboard_recent_suppliers_uses_first_contact_not_import_date():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    rows = _dashboard_rows({"SUPPLIERS": [
        {
            "CREATED_AT": now.isoformat(),
            "FIRST_CONTACT_DATE": (now - timedelta(days=120)).isoformat(),
        },
        {
            "CREATED_AT": now.isoformat(),
            "FIRST_CONTACT_DATE": (now - timedelta(days=2)).isoformat(),
        },
    ]})
    counts = {row["METRIC"]: row["VALUE"] for row in rows}
    assert counts["Новых поставщиков за 30 дней"] == 1


def test_large_backfill_expands_grid_before_reading_values():
    existing = {name: [headers] for name, headers in HEADERS.items()}
    session = _Session(existing)
    SupplierSheetsStore("sheet", Path("unused.json"), session=session).sync({
        "SUPPLIERS": [{"SUPPLIER_ID": f"SUP-{number:06d}"} for number in range(31)],
    })
    resize = next(body for url, body in session.writes if url.endswith("sheet:batchUpdate"))
    assert resize["requests"][0]["updateSheetProperties"]["properties"]["gridProperties"]["rowCount"] == 32
    assert any(request.get("setBasicFilter", {}).get("filter", {}).get("range", {}).get("endRowIndex") == 32
               for request in resize["requests"])
    assert any(request.get("updateFilterView", {}).get("filter", {}).get("range", {}).get("endRowIndex") == 32
               for request in resize["requests"])
    assert session.calls.index(("post", next(url for url, _ in session.writes if url.endswith("sheet:batchUpdate")))) < \
        session.calls.index(("get", next(url for method, url in session.calls if url.endswith("/values:batchGet"))))


def test_sheet_numeric_rendering_does_not_trigger_unnecessary_rewrites():
    assert _equal([100.0, 0.7, True], [100, 0.7, True])
    assert not _equal([True], [1])


def test_category_filter_has_one_row_per_supplier_category():
    rows = _category_filter_rows({
        "SUPPLIERS": [{"SUPPLIER_ID": "SUP-000001", "COMPANY_NAME": "Тест",
                       "PRIMARY_EMAIL": "info@test.ru", "TOTAL_RFQ": 2}],
        "SUPPLIER_CATEGORIES": [
            {"SUPPLIER_ID": "SUP-000001", "CATEGORY_L1": "Медицинское оборудование",
             "CATEGORY_L2": "Медицинские холодильники"},
            {"SUPPLIER_ID": "SUP-000001", "CATEGORY_L1": "Строительство",
             "CATEGORY_L2": "Строительные смеси"},
        ],
    })
    assert [row["FILTER_GROUP"] for row in rows] == ["Медоборудование", "Строительство"]
    assert all(row["COMPANY_NAME"] == "Тест" and row["PRIMARY_EMAIL"] == "info@test.ru" for row in rows)


def test_saved_category_filters_follow_expanded_taxonomy():
    existing = {name: [headers] for name, headers in HEADERS.items()}
    session = _Session(existing)
    SupplierSheetsStore("sheet", Path("unused.json"), session=session).sync({})
    views = [request["addFilterView"]["filter"]
             for url, body in session.writes if url.endswith("sheet:batchUpdate")
             for request in body["requests"] if "addFilterView" in request]
    names = {view["title"] for view in views}
    assert {"Медоборудование", "Строительство", "Климат", "Электроника",
            "Медпрепараты", "Упаковка", "ИТ и оргтехника", "СИЗ и спецодежда"} <= names
    assert all(view["filterSpecs"][0]["columnIndex"] == 0 for view in views)


def test_changed_email_remains_clickable_after_row_update():
    existing = {name: [headers] for name, headers in HEADERS.items()}
    session = _Session(existing)
    SupplierSheetsStore("sheet", Path("unused.json"), session=session).sync({
        "SUPPLIERS": [{"SUPPLIER_ID": "SUP-000001", "PRIMARY_EMAIL": "sales@example.org"}],
    })
    updates = [body for url, body in session.writes if url.endswith("/values:batchUpdate")]
    assert any(body["valueInputOption"] == "RAW" for body in updates)
    links = [part for body in updates if body["valueInputOption"] == "USER_ENTERED"
             for part in body["data"]]
    assert {"range": "'SUPPLIERS'!V2",
            "values": [[_email_formula("sales@example.org", "en_US")]]} in links
    assert _email_formula("sales@example.org", "ru_RU") == \
        '=HYPERLINK("mailto:sales@example.org";"sales@example.org")'
