from datetime import datetime, timezone

from tender_parser.outreach_watchdog import (
    _dashboard_datetime_age_hours,
    _delivery_counters,
    _latest_event_age_hours,
    _queue_counters,
    _recent_delivery_counters,
)
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
    failed[17] = "ошибка отправителя"

    assert _queue_counters([QUEUE_HEADERS, waiting, prepared, failed]) == (1, 1, 1)


def test_latest_event_age_uses_newest_matching_event() -> None:
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    rows = [
        ["Время", "ID", "Кампания", "Кандидат", "Хэш", "Действие"],
        ["05.09.2026 08:00:00", "1", "", "", "", "queue_sync_completed"],
        ["2026-09-05T10:00:00+00:00", "2", "", "", "", "queue_sync_completed"],
    ]
    assert _latest_event_age_hours(rows, "queue_sync_completed", now) == 2.0


def test_delivery_counters_include_legacy_and_russian_bounce_statuses() -> None:
    bounced = [""] * len(QUEUE_HEADERS)
    bounced[0] = "candidate-1"
    bounced[17] = "не доставлено"
    legacy = [""] * len(QUEUE_HEADERS)
    legacy[0] = "candidate-2"
    legacy[17] = "bounced"
    opted_out = [""] * len(QUEUE_HEADERS)
    opted_out[0] = "candidate-3"
    opted_out[17] = "не писать"

    assert _delivery_counters([QUEUE_HEADERS, bounced, legacy, opted_out]) == (2, 1)


def test_dashboard_monitor_age_reads_named_metric() -> None:
    now = datetime(2026, 9, 10, 16, tzinfo=timezone.utc)
    values = [
        ["Показатель", "Значение"],
        ["Последняя проверка возвратов", "2026-09-10T15:30:00+00:00"],
    ]

    assert _dashboard_datetime_age_hours(
        values, "Последняя проверка возвратов", now
    ) == 0.5


def test_recent_delivery_counters_measure_three_calendar_day_bounce_rate() -> None:
    now = datetime(2026, 9, 12, 22, tzinfo=timezone.utc)
    sent = [""] * len(QUEUE_HEADERS)
    sent[17] = "отправлено"
    sent[18] = "11.09.2026"
    bounced = [""] * len(QUEUE_HEADERS)
    bounced[17] = "не доставлено"
    bounced[18] = "10.09.2026"
    old = [""] * len(QUEUE_HEADERS)
    old[17] = "не доставлено"
    old[18] = "09.09.2026"

    assert _recent_delivery_counters(
        [QUEUE_HEADERS, sent, bounced, old], now
    ) == (2, 1)
