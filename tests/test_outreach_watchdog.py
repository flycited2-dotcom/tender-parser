from datetime import datetime, timezone

from tender_parser.outreach_watchdog import _latest_event_age_hours, _queue_counters
from tender_parser.outreach_queue import QUEUE_HEADERS


def test_queue_counters_separate_waiting_drafts_and_errors() -> None:
    waiting = [""] * len(QUEUE_HEADERS)
    waiting[0] = "candidate-1"
    waiting[4] = "buyer@example.ru"
    waiting[11] = "https://example.ru/contacts"
    waiting[12] = "https://zakupki.gov.ru/tender/1"
    waiting[15] = "needs_contact_review"
    waiting[17] = "заблокировано"
    prepared = [""] * len(QUEUE_HEADERS)
    prepared[0] = "candidate-2"
    prepared[17] = "рабочий черновик"
    prepared[20] = "draft-1"
    failed = [""] * len(QUEUE_HEADERS)
    failed[0] = "candidate-3"
    failed[17] = "ошибка отправки"

    assert _queue_counters([QUEUE_HEADERS, waiting, prepared, failed]) == (1, 1, 1)


def test_latest_event_age_uses_newest_matching_event() -> None:
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    rows = [
        ["Время", "ID", "Кампания", "Кандидат", "Хэш", "Действие"],
        ["05.09.2026 08:00:00", "1", "", "", "", "queue_sync_completed"],
        ["2026-09-05T10:00:00+00:00", "2", "", "", "", "queue_sync_completed"],
    ]
    assert _latest_event_age_hours(rows, "queue_sync_completed", now) == 2.0
