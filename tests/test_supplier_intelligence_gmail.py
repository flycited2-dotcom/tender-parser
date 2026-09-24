"""Offline tests for Gmail page contracts and attachment isolation."""

from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest
from googleapiclient.errors import HttpError
from openpyxl import Workbook

from tender_parser.supplier_intelligence.attachment_parser import (
    extract_attachment_text,
    extract_attachments,
    extract_spreadsheet_quote_lines,
)
from tender_parser.supplier_intelligence.gmail_collector import (
    AuthorizationRequired,
    GmailCollector,
    HistoryCursorExpired,
)
from tender_parser.supplier_intelligence.message_parser import normalize_gmail_message


def _collector() -> tuple[GmailCollector, MagicMock]:
    collector = GmailCollector(
        "termoark@gmail.com", "unused-client-secret.json", "unused-token.json"
    )
    service = MagicMock()
    collector._service = service
    return collector, service


def _encode(content: bytes) -> str:
    return base64.urlsafe_b64encode(content).decode("ascii").rstrip("=")


def _xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Прайс"
    sheet.append(["Модель", "Цена"])
    sheet.append(["Насос Н-100", 12345])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _commercial_offer_xlsx(*, mismatched_row: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "КП"
    sheet.append(["КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ"])
    sheet.append(["Дата: 10 августа 2026 г."])
    sheet.append([])
    sheet.append(["Товар / Артикул", "Фото", "Описание", "Кол-во", "Цена за ед.", "Сумма"])
    sheet.append([
        "Портативная станция Oukitel P2001EPlus\nPS_OK_P2001EPlus_2400", None,
        "Электростанция 2048 Вт·ч. Гарантия 2 000 ₽ на дополнительные услуги.",
        55, "98 670 ₽", "5 426 850 ₽",
    ])
    for number in range(2, 10):
        quantity = number
        price = number * 10_000
        sheet.append([
            f"Портативная станция Модель-{number}\nART_{number}_2000", None,
            "Мощность 2400 Вт, ресурс 4000 циклов", quantity,
            f"{price:,} ₽".replace(",", " "),
            f"{quantity * price:,} ₽".replace(",", " "),
        ])
    if mismatched_row:
        sheet.append(["Инвертор Solax X1", None, "Техническое описание", 2, "100 000 ₽", "250 000 ₽"])
    sheet.append(["ИТОГО (9 позиций)", None, None, None, None, "20 941 960 ₽"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _docx() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r>'
            '<w:t>ООО Поставщик</w:t></w:r></w:p></w:body></w:document>',
        )
    return output.getvalue()


def test_collector_requires_explicit_authorization() -> None:
    collector = GmailCollector("termoark@gmail.com", "unused", "unused")
    with pytest.raises(AuthorizationRequired):
        collector.get_message("message-id")


def test_message_listing_paginates_all_ordinary_mail_and_returns_history_cursor() -> None:
    collector, service = _collector()
    service.users().messages().list.return_value.execute.return_value = {
        "messages": [{"id": "inbound"}, {"id": "outbound"}],
        "nextPageToken": "page-2",
    }
    service.users().getProfile.return_value.execute.return_value = {
        "emailAddress": "termoark@gmail.com",
        "historyId": "991",
    }
    service.users().messages().list.return_value.execute.side_effect = lambda: (
        service.users().messages().list.return_value.execute.return_value
        if service.users().getProfile.called
        else pytest.fail("Backfill cursor must be captured before listing messages")
    )

    assert collector.list_message_ids(page_size=250) == (
        ["inbound", "outbound"], "page-2", "991"
    )
    service.users().messages().list.assert_called_once_with(
        userId="me", maxResults=250, includeSpamTrash=False
    )
    assert collector.list_message_ids("page-2", 250)[2] is None
    service.users().getProfile.assert_called_once_with(userId="me")


def test_history_collects_added_and_label_changed_ids_but_skips_deletions() -> None:
    collector, service = _collector()
    service.users().history().list.return_value.execute.return_value = {
        "history": [
            {"messagesAdded": [{"message": {"id": "new"}}]},
            {"labelsAdded": [{"message": {"id": "old"}}]},
            {"labelsRemoved": [{"message": {"id": "old"}}]},
            {"messagesDeleted": [{"message": {"id": "deleted"}}],
             "messages": [{"id": "deleted"}]},
        ],
        "nextPageToken": "more",
        "historyId": "1200",
    }

    assert collector.list_history("900") == (["new", "old"], "more", "1200")
    service.users().history().list.assert_called_once_with(
        userId="me", startHistoryId="900", maxResults=500
    )


def test_expired_history_has_a_distinct_fallback_signal() -> None:
    collector, service = _collector()
    service.users().history().list.return_value.execute.side_effect = HttpError(
        resp=SimpleNamespace(status=404, reason="Not Found"),
        content=b'{"error":{"message":"History ID too old"}}',
    )
    with pytest.raises(HistoryCursorExpired):
        collector.list_history("1")


def test_get_message_and_attachment_use_read_only_gmail_endpoints() -> None:
    collector, service = _collector()
    service.users().messages().get.return_value.execute.return_value = {"id": "m1"}
    service.users().messages().attachments().get.return_value.execute.return_value = {
        "data": "YWJj"
    }

    assert collector.get_message("m1") == {"id": "m1"}
    assert collector.get_attachment("m1", "a1") == {"data": "YWJj"}
    service.users().messages().get.assert_called_once_with(
        userId="me", id="m1", format="full"
    )
    service.users().messages().attachments().get.assert_called_once_with(
        userId="me", messageId="m1", id="a1"
    )


def test_rate_limit_is_retried_but_other_403_errors_are_not(monkeypatch) -> None:
    collector = GmailCollector("termoark@gmail.com", "unused", "unused",
                               min_request_interval_seconds=0)
    delays = []
    monkeypatch.setattr("tender_parser.supplier_intelligence.gmail_collector.time.sleep",
                        delays.append)
    monkeypatch.setattr("tender_parser.supplier_intelligence.gmail_collector.random.random",
                        lambda: 0)
    limited = HttpError(
        resp=SimpleNamespace(status=403, reason="Forbidden"),
        content=b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}',
    )
    request = MagicMock()
    request.execute.side_effect = [limited, limited, {"id": "m1"}]
    assert collector._execute(request) == {"id": "m1"}
    assert delays == [1, 2]

    forbidden = HttpError(
        resp=SimpleNamespace(status=403, reason="Forbidden"),
        content=b'{"error":{"errors":[{"reason":"insufficientPermissions"}]}}',
    )
    request.execute.side_effect = forbidden
    with pytest.raises(HttpError):
        collector._execute(request)
    assert delays == [1, 2]


def test_office_attachment_text_is_extracted_without_local_files() -> None:
    assert "Насос Н-100" in extract_attachment_text("price.xlsx", _xlsx())
    assert "12345" in extract_attachment_text("price.xlsx", _xlsx())
    assert "ООО Поставщик" in extract_attachment_text("offer.docx", _docx())


def test_xlsx_offer_uses_product_quantity_and_price_columns() -> None:
    quotes = extract_spreadsheet_quote_lines("КП_Электростанции_с_фото.xlsx", _commercial_offer_xlsx())
    assert len(quotes) == 9
    assert quotes[0]["product_name"] == "Портативная станция Oukitel P2001EPlus"
    assert quotes[0]["article"] == "PS_OK_P2001EPlus_2400"
    assert quotes[0]["quantity"] == 55
    assert quotes[0]["price_unit"] == 98_670
    assert quotes[0]["price_total"] == 5_426_850
    assert quotes[0]["currency"] == "RUB"
    assert quotes[0]["attachment_sheet"] == "КП"
    assert quotes[0]["attachment_row"] == 5
    assert all(quote["price_total"] == quote["quantity"] * quote["price_unit"] for quote in quotes)


def test_inconsistent_xlsx_price_is_unresolved_and_not_mistaken_for_quote() -> None:
    quotes = extract_spreadsheet_quote_lines("offer.xlsx", _commercial_offer_xlsx(mismatched_row=True))
    assert len(quotes) == 10
    invalid = quotes[-1]
    assert invalid["product_name"] == "Инвертор Solax X1"
    assert invalid["price_unit"] is None
    assert invalid["price_total"] is None
    assert invalid["unresolved_price"] == 100_000


def test_message_uses_structured_xlsx_quotes_without_flat_specification_prices() -> None:
    content = _commercial_offer_xlsx()
    body = _encode("Направляем КП во вложении.".encode("utf-8"))
    message = {
        "id": "offer-1", "threadId": "thread-1", "internalDate": "1790000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Отдел продаж <sales@vendor.example>"},
                {"name": "To", "value": "termoark@gmail.com"},
                {"name": "Subject", "value": "КП на электростанции"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": body}},
                {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "filename": "КП_Электростанции_с_фото.xlsx",
                 "body": {"data": _encode(content), "size": len(content)}},
            ],
        },
    }
    parsed = normalize_gmail_message(
        "termoark@gmail.com", message,
        own_emails={"termoark@gmail.com"}, own_domains=set(),
    )
    assert len(parsed["quote_lines"]) == 9
    assert all(quote["attachment_filename"] == "КП_Электростанции_с_фото.xlsx"
               for quote in parsed["quote_lines"])
    assert all(quote["attachment_type"] ==
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
               for quote in parsed["quote_lines"])


def test_attachment_failures_are_isolated_and_metadata_survives() -> None:
    price = _xlsx()
    message = {
        "id": "m1",
        "payload": {
            "mimeType": "multipart/mixed",
            "parts": [
                {"partId": "1", "filename": "price.xlsx",
                 "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "body": {"attachmentId": "a1", "size": len(price)}},
                {"partId": "2", "filename": "broken.pdf",
                 "mimeType": "application/pdf",
                 "body": {"data": _encode(b"not a PDF"), "size": 9}},
                {"partId": "3", "filename": "logo.png",
                 "mimeType": "image/png",
                 "body": {"attachmentId": "a3", "size": 12}},
            ],
        },
    }
    fetched: list[tuple[str, str]] = []

    def fetch(message_id: str, attachment_id: str):
        fetched.append((message_id, attachment_id))
        return {"data": _encode(price)}

    rows = extract_attachments(message, fetch)
    assert fetched == [("m1", "a1")]
    assert [(r["filename"], r["status"]) for r in rows] == [
        ("price.xlsx", "ok"), ("broken.pdf", "error"), ("logo.png", "unsupported")
    ]
    assert rows[0]["attachment_id"] == "a1"
    assert "Насос Н-100" in rows[0]["text"]
    assert rows[1]["text"] == ""
    assert extract_attachment_text("broken.pdf", b"not a PDF") == ""
