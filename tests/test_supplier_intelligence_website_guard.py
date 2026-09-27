"""Regression checks for website and company-name evidence in supplier mail."""

from __future__ import annotations

import base64

from tender_parser.supplier_intelligence.message_parser import normalize_gmail_message
from tender_parser.supplier_intelligence.signature_parser import (
    is_plausible_website,
    parse_signature,
)


def _raw_message(sender: str, subject: str, body: str) -> dict:
    encoded = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    return {
        "id": "mail-website-check",
        "threadId": "thread-website-check",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "termoark@gmail.com"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": encoded},
        },
    }


def _parsed(sender: str, subject: str, body: str) -> dict:
    return normalize_gmail_message(
        "termoark@gmail.com", _raw_message(sender, subject, body),
        own_emails={"termoark@gmail.com", "flycited@gmail.com"},
        own_domains={"simfer.com.ru"},
    )


def test_signature_rejects_sentence_fragments_and_public_mail_hosts() -> None:
    for value in ("день.благодарим", "день.для", "ул.клары", "яндекс.почты", "mail.ru"):
        assert not is_plausible_website(value)
        signature = parse_signature(
            f"С уважением,\nМенеджер\nООО «Поставщик»\nsales@company.ru\n{value}"
        )
        assert signature["website"] == ""


def test_signature_keeps_real_website_and_valid_idn() -> None:
    assert is_plausible_website("company.ru")
    assert is_plausible_website("пример.рф")
    assert parse_signature(
        "С уважением,\nМенеджер\nООО «Поставщик»\nsales@company.ru\nhttps://company.ru/catalog"
    )["website"] == "company.ru"
    assert parse_signature(
        "С уважением,\nМенеджер\nООО «Поставщик»\nsales@company.ru\nпример.рф"
    )["website"] == "пример.рф"


def test_public_mail_sender_label_is_not_company_or_website() -> None:
    parsed = _parsed("mail.ru <sales@company.ru>", "Цена оборудования", "Цена по запросу.")
    assert parsed["supplier"]["company_name"] == ""
    assert parsed["supplier"]["website"] == ""
    assert parsed["supplier"]["domain"] == "company.ru"


def test_legal_name_in_subject_is_not_assumed_to_be_supplier() -> None:
    parsed = _parsed(
        "Менеджер <sales@company.ru>",
        'ООО "Трансавто-7" за июнь',
        "Направляем коммерческое предложение.",
    )
    assert parsed["supplier"]["legal_name"] == ""
    assert parsed["supplier"]["company_name"] == ""


def test_quoted_signature_company_stops_at_closing_quote() -> None:
    parsed = parse_signature(
        "С уважением,\nОтдел продаж ООО 'СТЕРИМЕД.РУ'\n"
        "sales@sterimed.ru\n+7 (495) 201-20-30"
    )
    assert parsed["company"] == "ООО 'СТЕРИМЕД.РУ'"
