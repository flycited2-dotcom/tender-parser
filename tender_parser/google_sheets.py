from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import quote

import requests

from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult


SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
DATA_HEADERS = [
    "Ключ",
    "Новая",
    "Состояние",
    "Приоритет",
    "Название",
    "Регион",
    "Сумма",
    "Срок подачи",
    "Дней до срока",
    "Заказчик",
    "Категория",
    "Источник",
    "Номер",
    "Статус площадки",
    "Впервые найдено",
    "Последний раз найдено",
    "Мой выбор",
    "Комментарий",
    "Ссылка",
    "Причина включения",
]
PRIORITY_LABELS = {
    "hot": "Горячий",
    "review": "На проверку",
    "wide": "Широкий хвост",
    "excluded": "Отсеян",
}
SELECTED_CHOICES = {"Беру", "Думаю"}
TABLE_NAMES = {
    "Новые сегодня": "NewTodayTable",
    "Все актуальные": "ActiveTendersTable",
    "Мой отбор": "MySelectionTable",
    "Архив": "ArchiveTable",
}


@dataclass(frozen=True)
class GoogleSheetsConfig:
    enabled: bool = False
    spreadsheet_id: str = ""
    spreadsheet_url: str = ""
    service_account_file: Path | None = None
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls, base_dir: Path) -> "GoogleSheetsConfig":
        raw_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        credentials_path = Path(raw_path) if raw_path else None
        if credentials_path is not None and not credentials_path.is_absolute():
            credentials_path = base_dir / credentials_path
        return cls(
            enabled=_truthy(os.getenv("GOOGLE_SHEETS_ENABLED", "")),
            spreadsheet_id=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip(),
            spreadsheet_url=os.getenv("GOOGLE_SHEETS_URL", "").strip(),
            service_account_file=credentials_path,
            timeout_seconds=_positive_int(
                os.getenv("GOOGLE_SHEETS_TIMEOUT_SECONDS", ""), default=30
            ),
        )


SyncStatus = Literal["disabled", "synced", "error"]


@dataclass(frozen=True)
class GoogleSheetsSyncResult:
    status: SyncStatus
    active_count: int = 0
    new_count: int = 0
    archived_count: int = 0
    detail: str = ""
    spreadsheet_url: str = ""


class GoogleSheetsRegistry:
    def __init__(
        self,
        config: GoogleSheetsConfig,
        session: requests.Session | object | None = None,
    ) -> None:
        self.config = config
        self._session = session

    def sync(
        self,
        current: list[TenderRecord],
        fresh: list[TenderRecord],
        source_result: SourceFetchResult,
        *,
        generated_at: datetime,
        profile: str,
        raw_count: int | None = None,
        unique_count: int | None = None,
    ) -> GoogleSheetsSyncResult:
        if not self.config.enabled:
            return GoogleSheetsSyncResult(
                status="disabled", spreadsheet_url=self.config.spreadsheet_url
            )
        if not self.config.spreadsheet_id:
            return self._error("не задан GOOGLE_SHEETS_SPREADSHEET_ID")
        try:
            session = self._session or self._authorized_session()
            active_existing = self._get_values(session, "'Все актуальные'!A2:T1000")
            archive_existing = self._get_values(session, "'Архив'!A2:T1000")
            history_existing = self._get_values(session, "'История запусков'!A2:L1000")
            metadata = self._metadata(session)

            saved = _saved_fields([*active_existing, *archive_existing])
            fresh_keys = {tender.unique_key for tender in fresh}
            current_rows = [
                _record_row(tender, fresh_keys, saved, generated_at) for tender in current
            ]
            current_keys = {str(row[0]) for row in current_rows}

            archived_by_key = {
                str(row[0]): _pad(row, len(DATA_HEADERS))
                for row in archive_existing
                if row and row[0] and str(row[0]) not in current_keys
            }
            for existing in active_existing:
                row = _pad(existing, len(DATA_HEADERS))
                key = str(row[0] or "")
                if not key or key in current_keys:
                    continue
                row[1] = ""
                row[2] = "Не найдена в последнем запуске"
                archived_by_key[key] = row
            archive_rows = sorted(
                archived_by_key.values(), key=lambda row: str(row[15] or ""), reverse=True
            )

            current_by_key = {str(row[0]): row for row in current_rows}
            selection_rows = [
                row
                for row in [*current_by_key.values(), *archive_rows]
                if str(row[16] or "") in SELECTED_CHOICES
            ]
            fresh_rows = [row for row in current_rows if str(row[0]) in fresh_keys]

            history_rows = [row for row in history_existing if row and row[0]]
            history_stamp = _format_dt(generated_at)
            if not any(str(row[0]) == history_stamp for row in history_rows):
                history_rows.append(
                    _history_row(
                        source_result,
                        generated_at=generated_at,
                        profile=profile,
                        raw_count=raw_count,
                        unique_count=unique_count,
                        active_count=len(current_rows),
                        new_count=len(fresh_rows),
                    )
                )

            values_by_sheet = {
                "Новые сегодня": _with_row_formulas(
                    _rows_or_placeholder(fresh_rows, "Сегодня новых закупок нет", generated_at)
                ),
                "Все актуальные": _with_row_formulas(
                    _rows_or_placeholder(
                        current_rows, "Актуальных закупок пока нет", generated_at
                    )
                ),
                "Мой отбор": _with_row_formulas(
                    _rows_or_placeholder(
                        selection_rows, "Выбранных закупок пока нет", generated_at
                    )
                ),
                "Архив": _with_row_formulas(
                    _rows_or_placeholder(archive_rows, "Архив пока пуст", generated_at)
                ),
            }
            self._replace_values(session, values_by_sheet, history_rows)
            self._resize_tables(session, metadata, values_by_sheet, len(history_rows))
        except (OSError, ValueError, requests.RequestException) as exc:
            return self._error(exc.__class__.__name__)

        return GoogleSheetsSyncResult(
            status="synced",
            active_count=len(current_rows),
            new_count=len(fresh_rows),
            archived_count=len(archive_rows),
            detail="Google-реестр обновлён",
            spreadsheet_url=self.config.spreadsheet_url,
        )

    def _authorized_session(self) -> object:
        credentials_path = self.config.service_account_file
        if credentials_path is None or not credentials_path.is_file():
            raise OSError("service account file is missing")
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            str(credentials_path),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return AuthorizedSession(credentials)

    def _metadata(self, session: object) -> dict:
        response = session.get(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}",
            params={"includeGridData": "false"},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid spreadsheet metadata")
        return payload

    def _get_values(self, session: object, range_name: str) -> list[list[object]]:
        response = session.get(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}/values/{quote(range_name, safe='')}",
            params={"valueRenderOption": "UNFORMATTED_VALUE"},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        values = payload.get("values", []) if isinstance(payload, dict) else []
        return values if isinstance(values, list) else []

    def _replace_values(
        self,
        session: object,
        values_by_sheet: dict[str, list[list[object]]],
        history_rows: list[list[object]],
    ) -> None:
        data = [
            {"range": f"'{sheet}'!A2:T{len(rows) + 1}", "values": rows}
            for sheet, rows in values_by_sheet.items()
        ]
        if history_rows:
            data.append(
                {
                    "range": f"'История запусков'!A2:L{len(history_rows) + 1}",
                    "values": history_rows,
                }
            )
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}/values:batchUpdate",
            json={"valueInputOption": "USER_ENTERED", "data": data},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

        clear_specs = [
            (sheet, "T", len(rows) + 2) for sheet, rows in values_by_sheet.items()
        ]
        clear_specs.append(("История запусков", "L", len(history_rows) + 2))
        for sheet, columns, first_unused_row in clear_specs:
            if first_unused_row > 1000:
                continue
            clear_range = quote(
                f"'{sheet}'!A{first_unused_row}:{columns}1000", safe=""
            )
            response = session.post(  # type: ignore[attr-defined]
                f"{SHEETS_API}/{self.config.spreadsheet_id}/values/"
                f"{clear_range}:clear",
                json={},
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()

    def _resize_tables(
        self,
        session: object,
        metadata: dict,
        values_by_sheet: dict[str, list[list[object]]],
        history_count: int,
    ) -> None:
        located: dict[str, tuple[int, str]] = {}
        for sheet in metadata.get("sheets", []):
            title = str(sheet.get("properties", {}).get("title", ""))
            sheet_id = sheet.get("properties", {}).get("sheetId")
            for table in sheet.get("tables", []):
                if sheet_id is not None and table.get("tableId"):
                    located[str(table.get("name", ""))] = (int(sheet_id), str(table["tableId"]))

        requests_payload: list[dict[str, object]] = []
        for sheet, table_name in TABLE_NAMES.items():
            if table_name not in located:
                continue
            sheet_id, table_id = located[table_name]
            requests_payload.append(
                {
                    "updateTable": {
                        "table": {
                            "tableId": table_id,
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": len(values_by_sheet[sheet]) + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(DATA_HEADERS),
                            },
                        },
                        "fields": "range",
                    }
                }
            )
        if "RunHistoryTable" in located and history_count:
            sheet_id, table_id = located["RunHistoryTable"]
            requests_payload.append(
                {
                    "updateTable": {
                        "table": {
                            "tableId": table_id,
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": history_count + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": 12,
                            },
                        },
                        "fields": "range",
                    }
                }
            )
        if not requests_payload:
            return
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}:batchUpdate",
            json={"requests": requests_payload},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def _error(self, detail: str) -> GoogleSheetsSyncResult:
        return GoogleSheetsSyncResult(
            status="error",
            detail=detail,
            spreadsheet_url=self.config.spreadsheet_url,
        )


def _record_row(
    tender: TenderRecord,
    fresh_keys: set[str],
    saved: dict[str, tuple[object, object, object]],
    generated_at: datetime,
) -> list[object]:
    first_seen, choice, comment = saved.get(
        tender.unique_key,
        (_format_dt(tender.discovered_at or generated_at), "Не выбрано", ""),
    )
    return [
        tender.unique_key,
        "🆕" if tender.unique_key in fresh_keys else "",
        "Актуальна",
        PRIORITY_LABELS.get(tender.review_priority or "", tender.review_priority or ""),
        tender.title,
        tender.region or "",
        tender.price,
        _format_dt(tender.deadline),
        "",
        tender.customer or "",
        tender.category or "",
        tender.source,
        tender.tender_number or "",
        tender.status or "",
        first_seen,
        _format_dt(generated_at),
        choice or "Не выбрано",
        comment or "",
        tender.url,
        tender.include_reason,
    ]


def _saved_fields(rows: Iterable[list[object]]) -> dict[str, tuple[object, object, object]]:
    result: dict[str, tuple[object, object, object]] = {}
    for raw in rows:
        row = _pad(raw, len(DATA_HEADERS))
        key = str(row[0] or "")
        if key:
            result[key] = (row[14], row[16] or "Не выбрано", row[17] or "")
    return result


def _rows_or_placeholder(
    rows: list[list[object]], label: str, generated_at: datetime
) -> list[list[object]]:
    if rows:
        return rows
    result: list[object] = [""] * len(DATA_HEADERS)
    result[2] = "Нет данных"
    result[4] = label
    result[15] = _format_dt(generated_at)
    result[16] = "Не выбрано"
    return [result]


def _with_row_formulas(rows: list[list[object]]) -> list[list[object]]:
    result: list[list[object]] = []
    for row_number, raw in enumerate(rows, start=2):
        row = _pad(raw, len(DATA_HEADERS))
        if row[7]:
            row[8] = f'=IF(H{row_number}="","",INT(H{row_number}-TODAY()))'
        else:
            row[8] = ""
        result.append(row)
    return result


def _history_row(
    source_result: SourceFetchResult,
    *,
    generated_at: datetime,
    profile: str,
    raw_count: int | None,
    unique_count: int | None,
    active_count: int,
    new_count: int,
) -> list[object]:
    good = sum(item.status in {"ok", "empty"} for item in source_result.health)
    partial = sum(item.status in {"partial", "suspect_empty"} for item in source_result.health)
    bad_items = [
        item
        for item in source_result.health
        if item.status in {"error", "blocked", "timeout", "ssl_error"}
    ]
    return [
        _format_dt(generated_at),
        profile,
        len(source_result.tenders) if raw_count is None else raw_count,
        (
            len({tender.unique_key for tender in source_result.tenders})
            if unique_count is None
            else unique_count
        ),
        active_count,
        new_count,
        len(source_result.health),
        good,
        partial,
        len(bad_items),
        "Есть ошибки" if bad_items else "Успешно",
        "; ".join(f"{item.source}: {item.detail or item.status}" for item in bad_items),
    ]


def _pad(row: list[object], length: int) -> list[object]:
    return [*row[:length], *([""] * max(0, length - len(row)))]


def _format_dt(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if value else ""


def _truthy(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on", "да"}


def _positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
