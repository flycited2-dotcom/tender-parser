import csv
import hashlib
import json
from io import StringIO
from datetime import datetime

from tender_parser.outreach_candidates import (
    OUTREACH_HEADERS,
    build_legacy_horeca_suppression,
    build_outreach_candidates,
    write_outreach_handoff,
)
from tender_parser.cli import run
from tender_parser.customers import CUSTOMER_HEADERS


def customer_row(**overrides):
    values = {
        "organization_key": "гбуркцентр",
        "organization": 'ГБУ РК "Центр"',
        "organization_type": "Бюджетное учреждение",
        "region": "Республика Крым",
        "inn": "9100000000",
        "email": "purchase@centre.ru",
        "phone": "+7 978 000-00-00",
        "contact_person": "Отдел закупок",
        "website": "https://centre.ru",
        "contact_source": "https://centre.ru/contacts",
        "source_tender": "https://example.test/tender/1",
        "checked_at": "24.08.2026",
        "contact_status": "Готов к обращению",
        "note": "",
    }
    values.update(overrides)
    return [
        values["organization_key"],
        values["organization"],
        values["organization_type"],
        values["region"],
        values["inn"],
        "",
        "",
        values["email"],
        values["phone"],
        values["contact_person"],
        values["website"],
        values["contact_source"],
        values["source_tender"],
        values["checked_at"],
        values["contact_status"],
        values["note"],
    ]


def test_all_organization_types_require_evidenced_consent() -> None:
    result = build_outreach_candidates(
        [
            customer_row(),
            customer_row(
                organization_key="оочастное",
                organization='ООО "Частное"',
                organization_type="Коммерческая организация — ООО",
                inn="9100000001",
                email="sales@private-company.ru",
            ),
        ],
        campaign_id="pilot-1",
    )

    assert result.ready_for_campaign_review == 0
    assert result.needs_contact_review == 2
    assert {item.decision_reason for item in result.candidates} == {
        "consent_not_confirmed"
    }
    assert {item.organization_type for item in result.candidates} == {
        "Бюджетное учреждение",
        "Коммерческая организация — ООО",
    }
    assert all(item.approved_for_send is False for item in result.candidates)
    assert all(item.auto_send_allowed is False for item in result.candidates)


def test_old_horeca_touch_suppresses_new_tender_cold_outreach() -> None:
    headers = ["Email", "Название", "Статус", "Этап"]
    suppression = build_legacy_horeca_suppression(
        [
            ["sent@hotel.ru", "Отель", "отправлено", ""],
            ["reply@hotel.ru", "Гостевой дом", "отправлено", "ответил"],
            ["bad@hotel.ru", "Пансионат", "bounced", ""],
            ["new@hotel.ru", "Кемпинг", "новый", ""],
        ],
        headers,
    )
    result = build_outreach_candidates(
        [
            customer_row(email="sent@hotel.ru"),
            customer_row(organization_key="reply", email="reply@hotel.ru"),
            customer_row(organization_key="bad", email="bad@hotel.ru"),
            customer_row(organization_key="new", email="new@hotel.ru"),
        ],
        suppression=suppression,
    )
    decisions = {item.email: (item.decision, item.decision_reason) for item in result.candidates}

    assert decisions["sent@hotel.ru"] == ("suppressed", "previously_contacted_horeca")
    assert decisions["reply@hotel.ru"] == ("suppressed", "replied_horeca")
    assert decisions["bad@hotel.ru"] == ("suppressed", "bounced_horeca")
    assert decisions["new@hotel.ru"] == ("needs_contact_review", "consent_not_confirmed")


def test_unverified_contact_stays_out_of_campaign_queue() -> None:
    result = build_outreach_candidates(
        [customer_row(contact_status="Проверен")]
    )

    assert result.needs_contact_review == 1
    assert result.candidates[0].decision_reason == "contact_not_marked_ready"


def test_duplicate_email_keeps_the_stronger_contact_only() -> None:
    result = build_outreach_candidates(
        [
            customer_row(
                organization_key="needs-review",
                email="shared@organization.ru",
                contact_status="Нужно проверить",
            ),
            customer_row(
                organization_key="ready",
                email="shared@organization.ru",
                contact_status="Готов к обращению",
            ),
        ]
    )

    by_key = {item.organization_key: item for item in result.candidates}
    assert by_key["ready"].decision == "needs_contact_review"
    assert by_key["ready"].decision_reason == "consent_not_confirmed"
    assert by_key["needs-review"].decision == "excluded"
    assert by_key["needs-review"].decision_reason == "duplicate_email_in_current_registry"


def test_handoff_is_checksum_bound_and_never_approved(tmp_path) -> None:
    result = build_outreach_candidates([customer_row()], campaign_id="pilot-1")

    handoff = write_outreach_handoff(
        result,
        tmp_path,
        generated_at=datetime(2026, 8, 24, 20, 0),
        run_id="pilot-1",
    )

    payload = json.loads(handoff.manifest_path.read_text(encoding="utf-8"))
    with handoff.csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert list(rows[0]) == OUTREACH_HEADERS
    assert payload["approved_for_send"] is False
    assert payload["auto_send_allowed"] is False
    assert payload["approval_policy"] == "manual_campaign_approval_required"
    assert payload["dedup_policy"] == "global_horeca_and_tender_history"
    assert payload["candidates_csv_sha256"] == hashlib.sha256(
        handoff.csv_path.read_bytes()
    ).hexdigest()
    assert payload["idempotency_key"].endswith(handoff.sha256)


def test_cli_exports_connector_payload_from_stdin_without_sending(
    tmp_path, monkeypatch, capsys
) -> None:
    payload = {
        "customer_values": [
            CUSTOMER_HEADERS,
            customer_row(email="old@hotel.ru"),
            customer_row(organization_key="fresh", email="fresh@customer.ru"),
        ],
        "horeca_values": [
            ["Email", "Название", "Статус", "Этап"],
            ["old@hotel.ru", "Отель", "отправлено", ""],
        ],
    }
    monkeypatch.setattr(
        "sys.stdin", StringIO(json.dumps(payload, ensure_ascii=False) + "\n")
    )

    exit_code = run(
        [
            "outreach-export",
            "--base-dir",
            str(tmp_path),
            "--input-json",
            "-",
            "--campaign-id",
            "pilot",
            "--now",
            "2026-08-24T20:00:00",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "стоп-лист 1" in output
    assert "готовы к проверке кампании 0" in output
    manifest = json.loads(
        (tmp_path / "exports" / "outreach" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["approved_for_send"] is False
    assert manifest["auto_send_allowed"] is False
