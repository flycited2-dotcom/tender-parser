"""Batched, idempotent projection of SQLite facts into a dedicated Google Sheet."""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from requests.exceptions import ConnectionError as RequestConnectionError
from requests.exceptions import Timeout as RequestTimeout

from tender_parser.google_sheets import GoogleSheetsConfig, GoogleSheetsRegistry, SHEETS_API
from tender_parser.supplier_intelligence.product_classifier import category_groups
from tender_parser.supplier_intelligence.sheet_schema import HEADERS, column_letter


class SupplierSheetsStore:
    def __init__(
        self,
        spreadsheet_id: str,
        service_account_file: Path,
        *,
        session: object | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        if not spreadsheet_id:
            raise ValueError("SUPPLIER_SPREADSHEET_ID is missing")
        self.spreadsheet_id = spreadsheet_id
        self.service_account_file = service_account_file
        self.session = session
        self.timeout_seconds = timeout_seconds

    def sync(self, tables: dict[str, list[dict]]) -> dict[str, int]:
        """Write changed rows only, preserving manually selected review statuses."""
        session = self.session or self._authorized_session()
        response = _request(
            session,
            "get",
            f"{SHEETS_API}/{self.spreadsheet_id}",
            params={"includeGridData": "false", "fields": "spreadsheetId,properties(locale),sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)),basicFilter,filterViews)"},
            timeout=self.timeout_seconds,
        )
        sheets = {
            str(sheet.get("properties", {}).get("title") or ""): sheet
            for sheet in response.get("sheets", [])
        }
        missing = set(HEADERS) - set(sheets)
        if missing:
            raise ValueError(f"Supplier spreadsheet is missing tabs: {', '.join(sorted(missing))}")
        tables = {
            **tables,
            "CATEGORY_FILTER": _category_filter_rows(tables),
            "DASHBOARD": _dashboard_rows(tables),
        }
        # Values API ranges cannot extend beyond the current grid. Historical
        # imports often exceed the 1,000 rows created by a new spreadsheet.
        resize_requests = []
        for name, headers in HEADERS.items():
            sheet = sheets[name]
            properties = sheet["properties"]
            grid = properties.get("gridProperties") or {}
            current_rows = int(grid.get("rowCount") or 1)
            current_columns = int(grid.get("columnCount") or 1)
            needed_rows = len(tables.get(name, [])) + 1
            needed_columns = len(headers)
            if needed_rows > current_rows or needed_columns > current_columns:
                resize_requests.append({"updateSheetProperties": {
                    "properties": {
                        "sheetId": properties["sheetId"],
                        "gridProperties": {
                            "rowCount": max(current_rows, needed_rows),
                            "columnCount": max(current_columns, needed_columns),
                        },
                    },
                    "fields": "gridProperties(rowCount,columnCount)",
                }})
                new_rows = max(current_rows, needed_rows)
                basic_filter = sheet.get("basicFilter")
                if basic_filter:
                    updated_filter = {
                        **basic_filter,
                        "range": {**basic_filter["range"], "endRowIndex": new_rows},
                    }
                    resize_requests.append({"setBasicFilter": {"filter": updated_filter}})
                for view in sheet.get("filterViews") or []:
                    if view.get("filterViewId"):
                        resize_requests.append({"updateFilterView": {
                            "filter": {
                                "filterViewId": view["filterViewId"],
                                "range": {**view["range"], "endRowIndex": new_rows},
                            },
                            "fields": "range",
                        }})
                grid["rowCount"] = max(current_rows, needed_rows)
                grid["columnCount"] = max(current_columns, needed_columns)
        if resize_requests:
            _request(
                session, "post", f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate",
                json={"requests": resize_requests}, timeout=self.timeout_seconds,
            )
        self._ensure_category_filter_views(session, sheets["CATEGORY_FILTER"])
        ranges = []
        for name, headers in HEADERS.items():
            last_col = column_letter(len(headers))
            rows = max(int(sheets[name]["properties"].get("gridProperties", {}).get("rowCount") or 1), len(tables.get(name, [])) + 1)
            ranges.append(f"'{name}'!A1:{last_col}{rows}")
        existing_payload = _request(
            session,
            "get",
            f"{SHEETS_API}/{self.spreadsheet_id}/values:batchGet",
            params=[("ranges", value) for value in ranges] + [("valueRenderOption", "UNFORMATTED_VALUE")],
            timeout=self.timeout_seconds,
        )
        existing_ranges = existing_payload.get("valueRanges") or []
        if len(existing_ranges) != len(ranges):
            raise ValueError("Google Sheets returned an incomplete batch read")

        writes: list[dict] = []
        email_links: list[dict] = []
        clears: list[str] = []
        changed: dict[str, int] = {}
        locale = str(response.get("properties", {}).get("locale") or "en_US")
        for (name, headers), old_range in zip(HEADERS.items(), existing_ranges):
            old_values = old_range.get("values") or []
            if old_values and [str(value) for value in old_values[0]] != headers:
                raise ValueError(f"Unexpected header contract in {name}")
            desired = [headers] + [
                [_cell(row.get(header)) for header in headers]
                for row in tables.get(name, [])
            ]
            desired = _retain_review_decisions(name, headers, desired, old_values)
            edited = 0
            run_start: int | None = None
            run_rows: list[list[object]] = []

            def flush() -> None:
                nonlocal run_start, run_rows
                if run_start is None:
                    return
                final = run_start + len(run_rows) - 1
                writes.append({
                    "range": f"'{name}'!A{run_start}:{column_letter(len(headers))}{final}",
                    "values": run_rows,
                })
                run_start = None
                run_rows = []

            for number, row in enumerate(desired, start=1):
                old = _pad(old_values[number - 1], len(headers)) if number <= len(old_values) else [""] * len(headers)
                if _equal(row, old):
                    flush()
                    continue
                edited += 1
                for column, header in enumerate(headers, start=1):
                    if header not in {"PRIMARY_EMAIL", "EMAIL"}:
                        continue
                    email = str(row[column - 1] or "").strip()
                    if "@" in email and " " not in email:
                        email_links.append({
                            "range": f"'{name}'!{column_letter(column)}{number}",
                            "values": [[_email_formula(email, locale)]],
                        })
                if run_start is None:
                    run_start = number
                if len(run_rows) >= 400:
                    flush()
                    run_start = number
                run_rows.append(row)
            flush()
            if len(old_values) > len(desired):
                clears.append(f"'{name}'!A{len(desired)+1}:{column_letter(len(headers))}{len(old_values)}")
            changed[name] = edited
        for start in range(0, len(writes), 100):
            _request(
                session,
                "post",
                f"{SHEETS_API}/{self.spreadsheet_id}/values:batchUpdate",
                json={"valueInputOption": "RAW", "data": writes[start:start + 100]},
                timeout=self.timeout_seconds,
            )
        if clears:
            _request(
                session,
                "post",
                f"{SHEETS_API}/{self.spreadsheet_id}/values:batchClear",
                json={"ranges": clears},
                timeout=self.timeout_seconds,
            )
        for start in range(0, len(email_links), 400):
            _request(
                session, "post", f"{SHEETS_API}/{self.spreadsheet_id}/values:batchUpdate",
                json={"valueInputOption": "USER_ENTERED", "data": email_links[start:start + 400]},
                timeout=self.timeout_seconds,
            )
        return changed

    def _ensure_category_filter_views(self, session: object, sheet: dict) -> None:
        """Provide a saved filter for every procurement group in the taxonomy."""
        existing = {str(view.get("title") or "") for view in sheet.get("filterViews") or []}
        sheet_id = sheet["properties"]["sheetId"]
        row_count = int(sheet["properties"].get("gridProperties", {}).get("rowCount") or 1)
        groups = sorted({_FILTER_GROUPS.get(name, name) for name in category_groups()})
        requests = []
        for group in groups:
            if group in existing:
                continue
            requests.append({"addFilterView": {"filter": {
                "title": group,
                "range": {"sheetId": sheet_id, "startRowIndex": 0,
                          "endRowIndex": row_count, "startColumnIndex": 0,
                          "endColumnIndex": len(HEADERS["CATEGORY_FILTER"])},
                "filterSpecs": [{"columnIndex": 0, "filterCriteria": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": group}]},
                }}],
            }}})
        if requests:
            _request(session, "post", f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate",
                     json={"requests": requests}, timeout=self.timeout_seconds)

    def _authorized_session(self) -> object:
        config = GoogleSheetsConfig(
            enabled=True,
            spreadsheet_id=self.spreadsheet_id,
            service_account_file=self.service_account_file,
            timeout_seconds=self.timeout_seconds,
        )
        return GoogleSheetsRegistry(config)._authorized_session()


def _request(session: object, method: str, url: str, **kwargs: object) -> dict:
    """Back off only on transient Google failures; a bad sheet fails promptly."""
    for attempt in range(3):
        try:
            response = getattr(session, method)(url, **kwargs)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Google Sheets returned a non-object response")
            return payload
        except (OSError, TimeoutError, RequestConnectionError, RequestTimeout):
            if attempt >= 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("Google Sheets request failed")


def _cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return str(value)


def _email_formula(email: str, locale: str) -> str:
    escaped = email.replace('"', '""')
    separator = ";" if locale.casefold().startswith("ru") else ","
    return f'=HYPERLINK("mailto:{escaped}"{separator}"{escaped}")'


def _pad(row: list, width: int) -> list:
    return [*row[:width], *([""] * max(0, width - len(row)))]


def _equal(left: list, right: list) -> bool:
    def comparable(value: object) -> tuple[str, object]:
        if value is None:
            return ("text", "")
        if isinstance(value, bool):
            return ("bool", value)
        if isinstance(value, (int, float)):
            return ("number", Decimal(str(value)))
        return ("text", str(value))

    return [comparable(value) for value in left] == [comparable(value) for value in right]


def _retain_review_decisions(name: str, headers: list[str], desired: list[list], existing: list[list]) -> list[list]:
    if name not in {"SUPPLIERS", "REVIEW_QUEUE"} or len(existing) < 2:
        return desired
    status_header = "REVIEW_STATUS" if name == "SUPPLIERS" else "STATUS"
    status_index = headers.index(status_header)
    key_index = headers.index("SUPPLIER_ID" if name == "SUPPLIERS" else "REVIEW_ID")
    statuses = {
        str(row[key_index]): str(row[status_index])
        for row in existing[1:]
        if len(row) > max(key_index, status_index) and str(row[key_index]).strip() and str(row[status_index]).strip()
    }
    for row in desired[1:]:
        key = str(row[key_index])
        if statuses.get(key) and str(row[status_index] or "") in {"", "NEW"}:
            row[status_index] = statuses[key]
    return desired


def _dashboard_rows(tables: dict[str, list[dict]]) -> list[dict]:
    suppliers = tables.get("SUPPLIERS", [])
    contacts = tables.get("CONTACTS", [])
    categories = tables.get("SUPPLIER_CATEGORIES", [])
    quotes = tables.get("QUOTES", [])
    reviews = tables.get("REVIEW_QUEUE", [])
    snapshot = {
        str(row.get("METRIC") or ""): row.get("VALUE")
        for row in tables.get("DASHBOARD", [])
    }
    month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent = 0
    for supplier in suppliers:
        try:
            stamp = datetime.fromisoformat(str(supplier.get("FIRST_CONTACT_DATE") or ""))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            recent += stamp >= month_ago
        except ValueError:
            pass
    counts = Counter()
    category_paths: set[tuple[str, str, str, str]] = set()
    for category in categories:
        if category.get("CATEGORY_L1"):
            counts[str(category["CATEGORY_L1"])] += 1
            category_paths.add(tuple(str(category.get(f"CATEGORY_L{level}") or "") for level in range(1, 5)))
    rows = [
        ("Всего поставщиков", len(suppliers)),
        ("Всего контактов", len(contacts)),
        ("Всего товарных категорий", len(category_paths)),
        ("Связей поставщиков с категориями", len(categories)),
        ("Всего обработанных писем", int(snapshot.get("PROCESSED_MESSAGES") or 0)),
        ("Строк КП", len(quotes)),
        ("Писем с КП", len({
            (str(quote.get("GMAIL_ACCOUNT") or ""), str(quote.get("MESSAGE_ID") or ""))
            for quote in quotes if quote.get("MESSAGE_ID")
        })),
        ("Новых поставщиков за 30 дней", recent),
        ("Поставщики без категории", sum(not supplier.get("CATEGORY_L1") for supplier in suppliers)),
        ("Поставщики без телефона", sum(not supplier.get("PRIMARY_PHONE") for supplier in suppliers)),
        ("Поставщики без названия компании", sum(not supplier.get("COMPANY_NAME") for supplier in suppliers)),
        ("Ошибки обработки", int(snapshot.get("ERRORS") or 0)),
        ("Записи на ручной проверке", sum(str(item.get("STATUS") or "NEW") in {"", "NEW", "IN_REVIEW"} for item in reviews)),
    ]
    result = [{"METRIC": name, "VALUE": value, "DETAIL": ""} for name, value in rows]
    result.extend({"METRIC": "Категория", "VALUE": name, "DETAIL": amount} for name, amount in counts.most_common(20))
    return result


_FILTER_GROUPS = {
    "Медицинское оборудование": "Медоборудование",
    "Медицинские расходные материалы": "Медрасходники",
    "Климатическое оборудование": "Климат",
    "Хозтовары и уборка": "Хозтовары",
    "Спецодежда и СИЗ": "СИЗ и спецодежда",
}


def _category_filter_rows(tables: dict[str, list[dict]]) -> list[dict]:
    """One searchable row per supplier and category, including contact details."""
    suppliers = {
        str(row.get("SUPPLIER_ID") or ""): row
        for row in tables.get("SUPPLIERS", [])
    }
    rows: list[dict] = []
    for category in tables.get("SUPPLIER_CATEGORIES", []):
        supplier_id = str(category.get("SUPPLIER_ID") or "")
        supplier = suppliers.get(supplier_id)
        if not supplier:
            continue
        category_l1 = str(category.get("CATEGORY_L1") or "")
        if not category_l1:
            continue
        rows.append({
            "FILTER_GROUP": _FILTER_GROUPS.get(category_l1, category_l1),
            "CATEGORY_L1": category_l1,
            "CATEGORY_L2": category.get("CATEGORY_L2", ""),
            "CATEGORY_L3": category.get("CATEGORY_L3", ""),
            "CATEGORY_L4": category.get("CATEGORY_L4", ""),
            "COMPANY_NAME": supplier.get("COMPANY_NAME", ""),
            "SUPPLIER_ID": supplier_id,
            "BRANDS": supplier.get("BRANDS", ""),
            "PRIMARY_EMAIL": supplier.get("PRIMARY_EMAIL", ""),
            "PRIMARY_PHONE": supplier.get("PRIMARY_PHONE", ""),
            "LAST_CONTACT_DATE": supplier.get("LAST_CONTACT_DATE", ""),
            "TOTAL_RFQ": supplier.get("TOTAL_RFQ", 0),
            "TOTAL_QUOTES": supplier.get("TOTAL_QUOTES", 0),
            "SOURCE_THREAD_ID": category.get("SOURCE_THREAD_ID", ""),
            "SOURCE_MESSAGE_ID": category.get("SOURCE_MESSAGE_ID", ""),
            "CONFIDENCE": category.get("CONFIDENCE", ""),
            "REVIEW_STATUS": supplier.get("REVIEW_STATUS", ""),
        })
    return rows
