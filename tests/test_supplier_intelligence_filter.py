"""Noise filtering from real mailbox patterns, without blocking B2B offers."""

import base64

from tender_parser.supplier_intelligence.message_parser import normalize_gmail_message


def _message(sender: str, recipient: str, subject: str, body: str) -> dict:
    return {
        "id": "m", "threadId": "t", "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": recipient},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


def _parse(raw: dict) -> dict:
    return normalize_gmail_message(
        "termoark@gmail.com", raw,
        own_emails={"termoark@gmail.com", "flycited@gmail.com"},
        own_domains={"simfer.com.ru"},
    )


def test_google_account_notice_is_not_a_supplier():
    result = _parse(_message(
        'Google <no-reply@accounts.google.com>', 'termoark@gmail.com',
        'Новый вход в аккаунт', 'Для вашего аккаунта выполнен вход.',
    ))
    assert result["supplier_signal"] is False


def test_outbound_to_government_is_not_a_supplier():
    result = _parse(_message(
        'Termoark <termoark@gmail.com>', 'Office <office@akimokrug.zo.gov.ru>',
        'Коммерческое предложение на поставку', 'Предлагаем поставить оборудование.',
    ))
    assert result["supplier_signal"] is False


def test_b2b_price_newsletter_can_be_a_supplier():
    result = _parse(_message(
        'No reply <noreply@factory.example>', 'termoark@gmail.com',
        'Прайс на оборудование', 'Новый прайс и цены на поставку доступны по запросу.',
    ))
    assert result["supplier_signal"] is True


def test_configured_system_domain_is_ignored():
    result = normalize_gmail_message(
        "termoark@gmail.com",
        _message('Bank <news@bank.example>', 'termoark@gmail.com',
                 'Коммерческое предложение', 'Предлагаем новые услуги.'),
        own_emails={"termoark@gmail.com"}, own_domains=set(),
        ignored_sender_domains={"bank.example"},
    )
    assert result["supplier_signal"] is False
