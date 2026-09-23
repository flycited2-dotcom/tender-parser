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
)
from tender_parser.supplier_intelligence.gmail_collector import (
    AuthorizationRequired,
    GmailCollector,
    HistoryCursorExpired,
)


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
