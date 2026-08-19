from __future__ import annotations

from pathlib import Path

from tender_parser.notifications import NotificationConfig
from tender_parser.suppliers import SupplierIndexStatus
from tender_parser.supplier_inbox import SupplierIntakeResult
from tender_parser.telegram_bot import TelegramCommandBot
from tender_parser.telegram_bot import _supplier_id_from_caption


class NotifierSpy:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_text(self, text: str, **_: object) -> None:
        self.messages.append(text)


class CatalogSpy:
    last_query = ""

    def __init__(self, _: Path) -> None:
        pass

    def refresh(self) -> SupplierIndexStatus:
        return SupplierIndexStatus(status="ok")

    def search(self, query: str, *, limit: int = 10) -> list[object]:
        assert limit == 10
        CatalogSpy.last_query = query
        return []


class ResponseStub:
    def __init__(self, *, payload: dict | None = None, content: bytes = b"") -> None:
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class SessionStub:
    def get(self, url: str, **_: object) -> ResponseStub:
        if url.endswith("/getFile"):
            return ResponseStub(payload={"result": {"file_path": "documents/price.xlsx"}})
        return ResponseStub(content=b"xlsx-content")


class SupplierInboxSpy:
    received: tuple[bytes, str, str] | None = None

    def __init__(self, _: Path) -> None:
        pass

    def accept_bytes(self, payload: bytes, **metadata: object) -> SupplierIntakeResult:
        SupplierInboxSpy.received = (
            payload,
            str(metadata["filename"]),
            str(metadata["supplier_id"]),
        )
        return SupplierIntakeResult(
            status="accepted", supplier_id="promet", indexed_products=10, detail="готово"
        )


def test_price_command_preserves_query_arguments(tmp_path: Path, monkeypatch) -> None:
    bot = TelegramCommandBot(
        tmp_path,
        NotificationConfig(bot_token="token", chat_id="123"),
    )
    notifier = NotifierSpy()
    bot.notifier = notifier  # type: ignore[assignment]
    monkeypatch.setattr("tender_parser.telegram_bot.SupplierCatalog", CatalogSpy)

    bot._handle_update(
        {
            "message": {
                "chat": {"id": 123},
                "text": "/price@TenderAgentBot шкаф архивный 1850",
            }
        }
    )

    assert CatalogSpy.last_query == "шкаф архивный 1850"
    assert "ничего не найдено" in notifier.messages[0]


def test_help_mentions_supplier_search(tmp_path: Path) -> None:
    bot = TelegramCommandBot(
        tmp_path,
        NotificationConfig(bot_token="token", chat_id="123"),
    )
    notifier = NotifierSpy()
    bot.notifier = notifier  # type: ignore[assignment]

    bot._handle_update({"message": {"chat": {"id": 123}, "text": "/help"}})

    assert "/price шкаф архивный" in notifier.messages[0]
    assert "/pricefile promet" in notifier.messages[0]


def test_supplier_caption_parser_is_explicit() -> None:
    assert _supplier_id_from_caption("/pricefile promet") == "promet"
    assert _supplier_id_from_caption("#it_partner") == "it_partner"
    assert _supplier_id_from_caption("новый прайс") == ""


def test_document_in_configured_chat_is_imported(tmp_path: Path, monkeypatch) -> None:
    bot = TelegramCommandBot(
        tmp_path,
        NotificationConfig(bot_token="token", chat_id="123"),
        session=SessionStub(),  # type: ignore[arg-type]
    )
    notifier = NotifierSpy()
    bot.notifier = notifier  # type: ignore[assignment]
    monkeypatch.setattr("tender_parser.telegram_bot.SupplierInbox", SupplierInboxSpy)

    bot._handle_update(
        {
            "message": {
                "message_id": 77,
                "chat": {"id": 123},
                "from": {"id": 456},
                "caption": "/pricefile promet",
                "document": {
                    "file_id": "file-1",
                    "file_name": "new-price.xlsx",
                    "file_size": 100,
                },
            }
        }
    )

    assert SupplierInboxSpy.received == (b"xlsx-content", "new-price.xlsx", "promet")
    assert "accepted" in notifier.messages[0]
