from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Sequence
from urllib.parse import quote


SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
QUEUE_SHEET = "Очередь"
STOPLIST_SHEET = "Стоп-лист"
EVENTS_SHEET = "События"
DASHBOARD_SHEET = "Дашборд"
DEFAULT_CAMPAIGN_ID = "tender-intro-v1"

QUEUE_HEADERS = [
    "ID кандидата", "ID кампании", "Ключ организации", "ИНН", "Email",
    "Организация", "Тип организации", "Регион", "Телефон", "Контактное лицо",
    "Сайт", "Источник контакта", "Закупка-основание", "Дата проверки",
    "Статус контакта", "Решение", "Причина решения", "Статус рассылки",
    "Дата отправки", "Этап", "ID черновика", "ID сообщения", "ID цепочки",
    "Заметка", "Одобрено", "Автоотправка", "Основание обращения",
    "Статус согласия", "Подтверждение согласия", "Дата согласия",
]

CUSTOMER_HEADERS = [
    "Ключ организации", "Организация", "Тип", "Регион", "ИНН",
    "Юридический адрес", "Фактический / почтовый адрес", "Общий e-mail",
    "Телефон", "Контактное лицо / должность", "Официальный сайт",
    "Источник контактов", "Закупка-основание", "Дата проверки",
    "Статус контакта", "Примечание",
]

_EMAIL_RE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.I)
_SYSTEM_EMAIL_RE = re.compile(
    r"^(?:no-?reply|do-?not-?reply|mailer-daemon|postmaster|root)@|"
    r"@(?:example\.(?:com|net|org)|invalid|localhost)$",
    re.I,
)


@dataclass(frozen=True)
class OutreachQueueConfig:
    enabled: bool = False
    spreadsheet_id: str = ""
    service_account_file: Path | None = None
    campaign_id: str = DEFAULT_CAMPAIGN_ID
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls, base_dir: Path) -> "OutreachQueueConfig":
        raw_credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        credentials = Path(raw_credentials) if raw_credentials else None
        if credentials is not None and not credentials.is_absolute():
            credentials = base_dir / credentials
        spreadsheet_id = os.getenv("TENDER_OUTREACH_SPREADSHEET_ID", "").strip()
        enabled_value = os.getenv("TENDER_OUTREACH_QUEUE_SYNC_ENABLED", "").strip()
        return cls(
            enabled=_truthy(enabled_value) if enabled_value else bool(spreadsheet_id),
            spreadsheet_id=spreadsheet_id,
            service_account_file=credentials,
            campaign_id=(
                os.getenv("TENDER_OUTREACH_CAMPAIGN_ID", "").strip()
                or DEFAULT_CAMPAIGN_ID
            ),
            timeout_seconds=_positive_int(
                os.getenv("TENDER_OUTREACH_SYNC_TIMEOUT_SECONDS", ""), default=30
            ),
        )


QueueSyncStatus = Literal["disabled", "synced", "error"]


@dataclass(frozen=True)
class OutreachQueueSyncResult:
    status: QueueSyncStatus
    appended: int = 0
    updated: int = 0
    detail: str = ""


@dataclass(frozen=True)
class QueueUpdate:
    row_number: int
    values: list[object]


@dataclass(frozen=True)
class QueueSyncPlan:
    updates: list[QueueUpdate]
    appends: list[list[object]]


class OutreachQueueSynchronizer:
    """Append new tender customers to the mail queue without touching sent rows."""

    def __init__(self, config: OutreachQueueConfig, session: object | None = None) -> None:
        self.config = config
        self._session = session

    def sync(self, customer_rows: Sequence[Sequence[object]]) -> OutreachQueueSyncResult:
        if not self.config.enabled:
            return OutreachQueueSyncResult(status="disabled", detail="синхронизация отключена")
        if not self.config.spreadsheet_id:
            return OutreachQueueSyncResult(status="error", detail="не задан TENDER_OUTREACH_SPREADSHEET_ID")
        try:
            session = self._session or self._authorized_session()
            queue_values = self._get_values(session, QUEUE_SHEET, "A1:AD5000")
            stoplist_values = self._get_values(session, STOPLIST_SHEET, "A1:F5000")
            stop_emails = _stoplist_emails(stoplist_values)
            plan = build_queue_sync_plan(
                [CUSTOMER_HEADERS, *[list(row) for row in customer_rows]],
                queue_values,
                stop_emails=stop_emails,
                campaign_id=self.config.campaign_id,
            )
            if plan.updates:
                data = [
                    {
                        "range": f"'{QUEUE_SHEET}'!A{item.row_number}:AD{item.row_number}",
                        "values": [item.values],
                    }
                    for item in plan.updates
                ]
                response = session.post(  # type: ignore[attr-defined]
                    f"{SHEETS_API}/{self.config.spreadsheet_id}/values:batchUpdate",
                    json={"valueInputOption": "RAW", "data": data},
                    timeout=self.config.timeout_seconds,
                )
                response.raise_for_status()
            if plan.appends:
                last_data_row = max(
                    (
                        row_number
                        for row_number, row in enumerate(queue_values[1:], start=2)
                        if str(_cell(row, 0) or "").strip()
                    ),
                    default=1,
                )
                first_row = last_data_row + 1
                last_row = first_row + len(plan.appends) - 1
                queue_write_range = quote(
                    f"'{QUEUE_SHEET}'!A{first_row}:AD{last_row}", safe=""
                )
                response = session.put(  # type: ignore[attr-defined]
                    f"{SHEETS_API}/{self.config.spreadsheet_id}/values/"
                    + queue_write_range,
                    params={"valueInputOption": "RAW"},
                    json={"values": plan.appends},
                    timeout=self.config.timeout_seconds,
                )
                response.raise_for_status()
            self._update_dashboard(
                session,
                queue_values,
                plan,
                customer_count=sum(
                    bool(str(_cell(row, 0) or "").strip()) for row in customer_rows
                ),
                stoplist_count=len(stop_emails),
            )
            self._append_event(session, plan)
            return OutreachQueueSyncResult(
                status="synced",
                appended=len(plan.appends),
                updated=len(plan.updates),
                detail=f"добавлено {len(plan.appends)}, обновлено {len(plan.updates)}",
            )
        except Exception as exc:  # noqa: BLE001 - return a durable pipeline result
            return OutreachQueueSyncResult(status="error", detail=str(exc))

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

    def _get_values(self, session: object, sheet: str, cells: str) -> list[list[object]]:
        range_name = quote(f"'{sheet}'!{cells}", safe="")
        response = session.get(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}/values/{range_name}",
            params={"valueRenderOption": "FORMATTED_VALUE"},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return [list(row) for row in payload.get("values", [])]

    def _append_event(self, session: object, plan: QueueSyncPlan) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        event = [[
            timestamp,
            str(uuid.uuid4()),
            self.config.campaign_id,
            "",
            "",
            "queue_sync_completed",
            "",
            "",
            "tender-parser",
            f"updated={len(plan.updates)}; appended={len(plan.appends)}",
        ]]
        events_append_range = quote(f"'{EVENTS_SHEET}'!A:J", safe="")
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}/values/"
            f"{events_append_range}:append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": event},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def _update_dashboard(
        self,
        session: object,
        queue_values: Sequence[Sequence[object]],
        plan: QueueSyncPlan,
        *,
        customer_count: int,
        stoplist_count: int,
    ) -> None:
        rows = [list(row) for row in queue_values[1:]]
        for update in plan.updates:
            offset = update.row_number - 2
            while len(rows) <= offset:
                rows.append([])
            rows[offset] = update.values
        rows.extend(plan.appends)
        index = {name: position for position, name in enumerate(QUEUE_HEADERS)}
        actual = [row for row in rows if str(_cell(row, index["ID кандидата"]) or "").strip()]
        decision = lambda row: str(_cell(row, index["Решение"]) or "").strip()
        status = lambda row: str(_cell(row, index["Статус рассылки"]) or "").strip()
        eligible = sum(
            bool(normalize_email(_cell(row, index["Email"])))
            and decision(row) in {"needs_contact_review", "ready_for_campaign_review"}
            and status(row) in {"заблокировано", "в очереди"}
            and bool(str(_cell(row, index["Источник контакта"]) or "").strip())
            and str(_cell(row, index["Закупка-основание"]) or "").lower().startswith(("http://", "https://"))
            for row in actual
        )
        dashboard_values = [[value] for value in [
            customer_count,
            sum(decision(row) == "needs_contact_review" for row in actual),
            sum(decision(row) == "suppressed" for row in actual),
            stoplist_count,
            sum(decision(row) == "excluded" for row in actual),
            eligible,
            "АКТИВНА",
            True,
            sum(str(_cell(row, index["Статус согласия"]) or "").strip().casefold() == "подтверждено" for row in actual),
            0,
        ]]
        dashboard_range = quote(f"'{DASHBOARD_SHEET}'!B2:B11", safe="")
        response = session.put(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}/values/" + dashboard_range,
            params={"valueInputOption": "RAW"},
            json={"values": dashboard_values},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()


def build_queue_sync_plan(
    source_values: Sequence[Sequence[object]],
    queue_values: Sequence[Sequence[object]],
    *,
    stop_emails: set[str],
    campaign_id: str = DEFAULT_CAMPAIGN_ID,
) -> QueueSyncPlan:
    if not source_values or not queue_values:
        raise ValueError("источник заказчиков или очередь пусты")
    source_headers = [str(value or "").strip() for value in source_values[0]]
    queue_headers = [str(value or "").strip() for value in queue_values[0]]
    if source_headers[: len(CUSTOMER_HEADERS)] != CUSTOMER_HEADERS:
        raise ValueError("изменился формат листа потенциальных заказчиков")
    if queue_headers[: len(QUEUE_HEADERS)] != QUEUE_HEADERS:
        raise ValueError("изменился формат очереди рассылки")
    source_index = {name: index for index, name in enumerate(source_headers)}
    queue_index = {name: index for index, name in enumerate(queue_headers)}

    organization_rows: dict[str, tuple[int, Sequence[object]]] = {}
    claimed_emails: set[str] = set()
    for row_number, row in enumerate(queue_values[1:], start=2):
        key = _organization_key(_cell(row, queue_index["Ключ организации"]))
        email = normalize_email(_cell(row, queue_index["Email"]))
        if email:
            claimed_emails.add(email)
        if key:
            current = organization_rows.get(key)
            if current is None or _existing_row_priority(row, queue_index) > _existing_row_priority(current[1], queue_index):
                organization_rows[key] = (row_number, row)

    updates: list[QueueUpdate] = []
    appends: list[list[object]] = []
    source_rows = [
        row for row in source_values[1:]
        if _organization_key(_cell(row, source_index["Ключ организации"]))
    ]
    source_rows.sort(key=lambda row: _region_priority(_cell(row, source_index["Регион"])))
    for source_row in source_rows:
        key = _organization_key(_cell(source_row, source_index["Ключ организации"]))
        source_email = normalize_email(_cell(source_row, source_index["Общий e-mail"]))
        existing = organization_rows.get(key)
        if existing is not None:
            row_number, old_row = existing
            old_email = normalize_email(_cell(old_row, queue_index["Email"]))
            protected = any(
                _cell(old_row, queue_index[name])
                for name in ("Дата отправки", "ID черновика", "ID сообщения")
            )
            if not old_email and source_email and not protected:
                values = _queue_row(
                    source_row, source_index, queue_headers, stop_emails,
                    claimed_emails, campaign_id,
                )
                updates.append(QueueUpdate(row_number=row_number, values=values))
                organization_rows[key] = (row_number, values)
            continue
        values = _queue_row(
            source_row, source_index, queue_headers, stop_emails,
            claimed_emails, campaign_id,
        )
        appends.append(values)
        organization_rows[key] = (0, values)
    return QueueSyncPlan(updates=updates, appends=appends)


def normalize_email(value: object) -> str:
    match = _EMAIL_RE.search(str(value or "").strip().casefold())
    return match.group(0).rstrip(".") if match else ""


def _queue_row(
    source_row: Sequence[object],
    source_index: dict[str, int],
    queue_headers: Sequence[str],
    stop_emails: set[str],
    claimed_emails: set[str],
    campaign_id: str,
) -> list[object]:
    value = lambda name: _cell(source_row, source_index[name])
    key = _organization_key(value("Ключ организации"))
    email = normalize_email(value("Общий e-mail"))
    contact_source = str(value("Источник контактов") or "").strip()
    source_tender = str(value("Закупка-основание") or "").strip()
    decision, reason = "needs_contact_review", "new_public_tender_contact"
    if not email:
        decision, reason = "excluded", "missing_or_invalid_email"
    elif _SYSTEM_EMAIL_RE.search(email):
        decision, reason = "excluded", "system_or_placeholder_email"
    elif email in stop_emails:
        decision, reason = "suppressed", "global_stoplist"
    elif email in claimed_emails:
        decision, reason = "excluded", "duplicate_email_in_current_registry"
    elif not contact_source or not source_tender.lower().startswith(("http://", "https://")):
        decision, reason = "excluded", "public_tender_evidence_missing"
    if email:
        claimed_emails.add(email)
    candidate_id = hashlib.sha256(
        f"tender-outreach-v1|{key}|{email}".encode("utf-8")
    ).hexdigest()[:24]
    values: dict[str, object] = {
        "ID кандидата": candidate_id,
        "ID кампании": campaign_id,
        "Ключ организации": key,
        "ИНН": value("ИНН"),
        "Email": email,
        "Организация": value("Организация"),
        "Тип организации": value("Тип"),
        "Регион": value("Регион"),
        "Телефон": value("Телефон"),
        "Контактное лицо": value("Контактное лицо / должность"),
        "Сайт": value("Официальный сайт"),
        "Источник контакта": contact_source,
        "Закупка-основание": source_tender,
        "Дата проверки": value("Дата проверки"),
        "Статус контакта": value("Статус контакта"),
        "Решение": decision,
        "Причина решения": reason,
        "Статус рассылки": "заблокировано",
        "Этап": "новый",
        "Заметка": value("Примечание"),
        "Одобрено": False,
        "Автоотправка": False,
        "Основание обращения": "публичный контакт закупки",
        "Статус согласия": "неизвестно",
    }
    return [values.get(header, "") for header in queue_headers]


def _stoplist_emails(values: Sequence[Sequence[object]]) -> set[str]:
    if not values:
        return set()
    headers = [str(value or "").strip() for value in values[0]]
    try:
        email_index = headers.index("Email")
    except ValueError:
        email_index = 0
    return {
        email for row in values[1:]
        if (email := normalize_email(_cell(row, email_index)))
    }


def _existing_row_priority(row: Sequence[object], index: dict[str, int]) -> tuple[int, int]:
    return (
        int(bool(_cell(row, index["Дата отправки"]) or _cell(row, index["ID сообщения"]))),
        int(bool(normalize_email(_cell(row, index["Email"])))),
    )


def _organization_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _region_priority(value: object) -> int:
    region = str(value or "").strip().casefold()
    if "крым" in region or "севастопол" in region:
        return 0
    if "запорож" in region or "херсон" in region:
        return 1
    return 2


def _cell(row: Sequence[object], index: int) -> object:
    return row[index] if 0 <= index < len(row) else ""


def _truthy(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on", "да"}


def _positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
