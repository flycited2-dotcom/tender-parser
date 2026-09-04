from tender_parser.outreach_queue import (
    CUSTOMER_HEADERS,
    QUEUE_HEADERS,
    build_queue_sync_plan,
)


def source_row(key: str, email: str, region: str = "Республика Крым") -> list[object]:
    return [
        key,
        f"Организация {key}",
        "Орган власти",
        region,
        "123",
        "",
        "",
        email,
        "+7",
        "Контакт",
        "https://example.ru",
        "https://example.ru/contacts",
        "https://zakupki.gov.ru/tender/1",
        "2026-09-05",
        "Нужно проверить",
        "",
    ]


def empty_queue_row(key: str) -> list[object]:
    row = [""] * len(QUEUE_HEADERS)
    row[0] = "old-id"
    row[1] = "tender-intro-v1"
    row[2] = key
    row[5] = "Старая организация"
    row[17] = "заблокировано"
    return row


def test_sync_appends_new_organizations_and_enriches_empty_contact() -> None:
    plan = build_queue_sync_plan(
        [
            CUSTOMER_HEADERS,
            source_row("new-zap", "new@example.ru", "Запорожская область"),
            source_row("new-crimea", "crimea@example.ru"),
            source_row("old-org", "found@example.ru"),
        ],
        [QUEUE_HEADERS, empty_queue_row("old-org")],
        stop_emails=set(),
    )

    assert len(plan.updates) == 1
    assert plan.updates[0].row_number == 2
    assert plan.updates[0].values[4] == "found@example.ru"
    assert len(plan.appends) == 2
    assert plan.appends[0][2] == "new-crimea"
    assert plan.appends[1][2] == "new-zap"
    assert plan.appends[0][15] == "needs_contact_review"


def test_sync_never_replaces_or_duplicates_sent_organization() -> None:
    existing = empty_queue_row("sent-org")
    existing[4] = "old@example.ru"
    existing[18] = "03.09.2026"
    existing[21] = "message-id"
    plan = build_queue_sync_plan(
        [CUSTOMER_HEADERS, source_row("sent-org", "new@example.ru")],
        [QUEUE_HEADERS, existing],
        stop_emails=set(),
    )
    assert plan.updates == []
    assert plan.appends == []


def test_sync_respects_global_stoplist_and_internal_email_deduplication() -> None:
    plan = build_queue_sync_plan(
        [
            CUSTOMER_HEADERS,
            source_row("blocked", "stop@example.ru"),
            source_row("first", "same@example.ru"),
            source_row("second", "same@example.ru"),
        ],
        [QUEUE_HEADERS],
        stop_emails={"stop@example.ru"},
    )
    by_key = {row[2]: row for row in plan.appends}
    assert by_key["blocked"][15:18] == ["suppressed", "global_stoplist", "заблокировано"]
    assert by_key["first"][15] == "needs_contact_review"
    assert by_key["second"][15:17] == ["excluded", "duplicate_email_in_current_registry"]
