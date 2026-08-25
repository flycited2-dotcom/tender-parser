from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

from tender_parser.customers import CUSTOMER_HEADERS


SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


@dataclass(frozen=True)
class GoogleOutreachSourceConfig:
    customer_spreadsheet_id: str
    horeca_spreadsheet_id: str
    service_account_file: Path | None
    customer_sheet_name: str = "Потенциальные заказчики"
    horeca_sheet_name: str = "Рассылка"
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls, base_dir: Path) -> "GoogleOutreachSourceConfig":
        raw_credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        credentials = Path(raw_credentials) if raw_credentials else None
        if credentials is not None and not credentials.is_absolute():
            credentials = base_dir / credentials
        return cls(
            customer_spreadsheet_id=os.getenv(
                "GOOGLE_SHEETS_SPREADSHEET_ID", ""
            ).strip(),
            horeca_spreadsheet_id=os.getenv(
                "HORECA_MAILING_SPREADSHEET_ID", ""
            ).strip(),
            service_account_file=credentials,
            customer_sheet_name=(
                os.getenv("OUTREACH_CUSTOMER_SHEET_NAME", "").strip()
                or "Потенциальные заказчики"
            ),
            horeca_sheet_name=(
                os.getenv("HORECA_MAILING_SHEET_NAME", "").strip() or "Рассылка"
            ),
            timeout_seconds=_positive_int(
                os.getenv("GOOGLE_SHEETS_TIMEOUT_SECONDS", ""), default=30
            ),
        )


@dataclass(frozen=True)
class OutreachSourceSnapshot:
    customer_headers: list[object]
    customer_rows: list[list[object]]
    horeca_headers: list[object]
    horeca_rows: list[list[object]]

    @classmethod
    def from_values(
        cls,
        customer_values: Sequence[Sequence[object]],
        horeca_values: Sequence[Sequence[object]],
    ) -> "OutreachSourceSnapshot":
        if not customer_values:
            raise ValueError("customer sheet is empty")
        if not horeca_values:
            raise ValueError("HoReCa mailing sheet is empty")
        customer_headers = list(customer_values[0])
        horeca_headers = list(horeca_values[0])
        _validate_customer_headers(customer_headers)
        _validate_horeca_headers(horeca_headers)
        return cls(
            customer_headers=customer_headers,
            customer_rows=[
                list(row)
                for row in customer_values[1:]
                if any(str(value or "").strip() for value in row)
            ],
            horeca_headers=horeca_headers,
            horeca_rows=[
                list(row)
                for row in horeca_values[1:]
                if any(str(value or "").strip() for value in row)
            ],
        )

    @classmethod
    def from_payload(cls, payload: object) -> "OutreachSourceSnapshot":
        if not isinstance(payload, dict):
            raise ValueError("outreach input must be a JSON object")
        customer_values = payload.get("customer_values")
        horeca_values = payload.get("horeca_values")
        if not isinstance(customer_values, list) or not isinstance(horeca_values, list):
            raise ValueError("outreach input requires customer_values and horeca_values")
        return cls.from_values(customer_values, horeca_values)


class GoogleSheetsOutreachReader:
    """Read the two source ledgers with a spreadsheets.readonly credential."""

    def __init__(
        self,
        config: GoogleOutreachSourceConfig,
        session: object | None = None,
    ) -> None:
        self.config = config
        self._session = session

    def read(self) -> OutreachSourceSnapshot:
        if not self.config.customer_spreadsheet_id:
            raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID is missing")
        if not self.config.horeca_spreadsheet_id:
            raise ValueError("HORECA_MAILING_SPREADSHEET_ID is missing")
        session = self._session or self._authorized_session()
        customer_rows = self._sheet_row_count(
            session,
            self.config.customer_spreadsheet_id,
            self.config.customer_sheet_name,
        )
        horeca_rows = self._sheet_row_count(
            session,
            self.config.horeca_spreadsheet_id,
            self.config.horeca_sheet_name,
        )
        customer_values = self._get_values(
            session,
            self.config.customer_spreadsheet_id,
            self.config.customer_sheet_name,
            f"A1:P{customer_rows}",
        )
        horeca_values = self._get_values(
            session,
            self.config.horeca_spreadsheet_id,
            self.config.horeca_sheet_name,
            f"A1:M{horeca_rows}",
        )
        return OutreachSourceSnapshot.from_values(customer_values, horeca_values)

    def _authorized_session(self) -> object:
        credentials_path = self.config.service_account_file
        if credentials_path is None or not credentials_path.is_file():
            raise OSError("service account file is missing")
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            str(credentials_path),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        return AuthorizedSession(credentials)

    def _sheet_row_count(
        self,
        session: object,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> int:
        response = session.get(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{spreadsheet_id}",
            params={"includeGridData": "false"},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid spreadsheet metadata")
        for sheet in payload.get("sheets", []):
            properties = sheet.get("properties", {})
            if str(properties.get("title", "")) != sheet_name:
                continue
            rows = properties.get("gridProperties", {}).get("rowCount")
            if not isinstance(rows, int) or rows < 1:
                raise ValueError(f"invalid row count for sheet {sheet_name}")
            return min(rows, 5000)
        raise ValueError(f"sheet not found: {sheet_name}")

    def _get_values(
        self,
        session: object,
        spreadsheet_id: str,
        sheet_name: str,
        range_name: str,
    ) -> list[list[object]]:
        full_range = quote(f"'{sheet_name}'!{range_name}", safe="")
        response = session.get(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{spreadsheet_id}/values/{full_range}",
            params={"valueRenderOption": "FORMATTED_VALUE"},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        values = payload.get("values", []) if isinstance(payload, dict) else []
        if not isinstance(values, list):
            raise ValueError("invalid spreadsheet values")
        return [list(row) for row in values if isinstance(row, list)]


def _validate_customer_headers(headers: Sequence[object]) -> None:
    actual = [str(value or "").strip() for value in headers[: len(CUSTOMER_HEADERS)]]
    if actual != CUSTOMER_HEADERS:
        raise ValueError("unexpected Potential Customers header contract")


def _validate_horeca_headers(headers: Sequence[object]) -> None:
    actual = {str(value or "").strip() for value in headers}
    missing = {"Email", "Статус", "Этап"} - actual
    if missing:
        raise ValueError(f"HoReCa mailing headers are missing: {sorted(missing)}")


def _positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default

