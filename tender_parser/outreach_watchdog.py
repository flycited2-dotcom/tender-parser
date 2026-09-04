from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

from tender_parser.env import load_env_file
from tender_parser.notifications import NotificationConfig, TelegramNotifier
from tender_parser.outreach_queue import (
    OutreachQueueConfig,
    OutreachQueueSynchronizer,
    SHEETS_API,
)


@dataclass(frozen=True)
class WatchdogReport:
    checked_at: str
    healthy: bool
    collector_age_hours: float | None
    enrichment_age_hours: float | None
    queue_sync_age_hours: float | None
    eligible_waiting: int
    prepared_drafts: int
    send_errors: int
    repairs: tuple[str, ...]
    problems: tuple[str, ...]


def run_watchdog(base_dir: Path, *, now: datetime | None = None) -> WatchdogReport:
    load_env_file(base_dir / ".env")
    load_env_file(base_dir / ".env.local")
    current = now or datetime.now().astimezone()
    repairs: list[str] = []
    problems: list[str] = []

    collector_age = _file_age_hours(base_dir / "data" / "scheduler_state.json", current)
    enrichment_age = _file_age_hours(
        Path(os.getenv("CUSTOMER_CONTACTS_CACHE_PATH", "data/customer_contacts.json"))
        if Path(os.getenv("CUSTOMER_CONTACTS_CACHE_PATH", "data/customer_contacts.json")).is_absolute()
        else base_dir / os.getenv("CUSTOMER_CONTACTS_CACHE_PATH", "data/customer_contacts.json"),
        current,
    )
    if collector_age is None or collector_age > 26:
        if _start_collector_task():
            repairs.append("collector_started")
        else:
            problems.append("сборщик не обновлялся более 26 часов и не запустился повторно")
    if enrichment_age is None or enrichment_age > 30:
        problems.append("обогащение контактов не обновлялось более 30 часов")

    queue_config = OutreachQueueConfig.from_env(base_dir)
    eligible_waiting = 0
    prepared_drafts = 0
    send_errors = 0
    queue_sync_age: float | None = None
    try:
        session = OutreachQueueSynchronizer(queue_config)._authorized_session()
        queue_values = _get_values(
            session, queue_config.spreadsheet_id, "'Очередь'!A1:AD5000",
            queue_config.timeout_seconds,
        )
        event_values = _get_values(
            session, queue_config.spreadsheet_id, "'События'!A1:J5000",
            queue_config.timeout_seconds,
        )
        queue_sync_age = _latest_event_age_hours(event_values, "queue_sync_completed", current)
        eligible_waiting, prepared_drafts, send_errors = _queue_counters(queue_values)

        if queue_sync_age is None or queue_sync_age > 8:
            customer_sheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
            customer_values = _get_values(
                session, customer_sheet_id, "'Потенциальные заказчики'!A1:P5000",
                queue_config.timeout_seconds,
            )
            result = OutreachQueueSynchronizer(queue_config, session=session).sync(
                customer_values[1:]
            )
            if result.status == "synced":
                repairs.append("queue_resynced")
                queue_sync_age = 0.0
            else:
                problems.append(f"повторная синхронизация очереди не выполнена: {result.detail}")
        if send_errors:
            problems.append(f"в очереди ошибок подготовки/отправки: {send_errors}")
        if eligible_waiting > 0 and prepared_drafts == 0:
            last_draft_age = _latest_event_age_hours(
                event_values, "automated_working_draft_created", current
            )
            grace_elapsed = queue_sync_age is None or queue_sync_age > 6
            if grace_elapsed and (last_draft_age is None or last_draft_age > 30):
                problems.append(
                    f"почтовый контур не подготовил черновики; ожидают {eligible_waiting} адресатов"
                )
    except Exception as exc:  # noqa: BLE001 - watchdog must keep reporting
        problems.append(f"контроль очереди недоступен: {exc.__class__.__name__}")

    report = WatchdogReport(
        checked_at=current.isoformat(timespec="seconds"),
        healthy=not problems,
        collector_age_hours=collector_age,
        enrichment_age_hours=enrichment_age,
        queue_sync_age_hours=queue_sync_age,
        eligible_waiting=eligible_waiting,
        prepared_drafts=prepared_drafts,
        send_errors=send_errors,
        repairs=tuple(repairs),
        problems=tuple(problems),
    )
    _persist_and_notify(base_dir, report)
    return report


def _get_values(
    session: object,
    spreadsheet_id: str,
    range_name: str,
    timeout_seconds: int,
) -> list[list[object]]:
    if not spreadsheet_id:
        raise ValueError("spreadsheet id is missing")
    response = session.get(  # type: ignore[attr-defined]
        f"{SHEETS_API}/{spreadsheet_id}/values/{quote(range_name, safe='')}",
        params={"valueRenderOption": "FORMATTED_VALUE"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return [list(row) for row in response.json().get("values", [])]


def _queue_counters(values: Sequence[Sequence[object]]) -> tuple[int, int, int]:
    if not values:
        return 0, 0, 0
    headers = {str(value or "").strip(): index for index, value in enumerate(values[0])}
    eligible = prepared = errors = 0
    for row in values[1:]:
        if not _cell(row, headers, "ID кандидата"):
            continue
        decision = str(_cell(row, headers, "Решение") or "").strip()
        status = str(_cell(row, headers, "Статус рассылки") or "").strip()
        email = str(_cell(row, headers, "Email") or "").strip()
        source = str(_cell(row, headers, "Источник контакта") or "").strip()
        tender = str(_cell(row, headers, "Закупка-основание") or "").strip()
        if (
            email
            and source
            and tender.lower().startswith(("http://", "https://"))
            and decision in {"needs_contact_review", "ready_for_campaign_review"}
            and status in {"заблокировано", "в очереди"}
        ):
            eligible += 1
        if status == "рабочий черновик" and _cell(row, headers, "ID черновика"):
            prepared += 1
        if status in {"ошибка рабочего черновика", "ошибка отправки"}:
            errors += 1
    return eligible, prepared, errors


def _latest_event_age_hours(
    values: Sequence[Sequence[object]], event_type: str, now: datetime
) -> float | None:
    latest: datetime | None = None
    for row in values[1:]:
        if len(row) < 6 or str(row[5] or "").strip() != event_type:
            continue
        parsed = _parse_datetime(str(row[0] or ""), now)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return _age_hours(latest, now)


def _parse_datetime(value: str, now: datetime) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None and now.tzinfo is not None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed


def _file_age_hours(path: Path, now: datetime) -> float | None:
    if not path.is_file():
        return None
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
    return _age_hours(modified, now)


def _age_hours(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    return max((now - value).total_seconds() / 3600, 0.0)


def _start_collector_task() -> bool:
    try:
        result = subprocess.run(
            ["schtasks.exe", "/Run", "/TN", "Tender Parser Daily"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _persist_and_notify(base_dir: Path, report: WatchdogReport) -> None:
    state_path = base_dir / "data" / "outreach_watchdog_state.json"
    previous: dict[str, object] = {}
    if state_path.is_file():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    signature = "|".join(report.problems)
    previous_signature = str(previous.get("problem_signature", ""))
    previous_notice = _parse_datetime(str(previous.get("last_notice_at", "")), datetime.now().astimezone())
    should_notify = bool(signature) and (
        signature != previous_signature
        or previous_notice is None
        or datetime.now().astimezone() - previous_notice >= timedelta(hours=24)
    )
    last_notice = str(previous.get("last_notice_at", ""))
    if should_notify:
        message = "⚠️ Контроль тендерной рассылки\n\n" + "\n".join(
            f"• {problem}" for problem in report.problems
        )
        result = TelegramNotifier(NotificationConfig.from_env()).send_text(message)
        if result.status == "sent":
            last_notice = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        **asdict(report),
        "problem_signature": signature,
        "last_notice_at": last_notice,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, state_path)


def _cell(row: Sequence[object], headers: dict[str, int], name: str) -> object:
    index = headers.get(name, -1)
    return row[index] if 0 <= index < len(row) else ""


def main() -> int:
    base_dir = Path(__file__).resolve().parents[1]
    report = run_watchdog(base_dir)
    print(json.dumps(asdict(report), ensure_ascii=False))
    return 0 if report.healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
