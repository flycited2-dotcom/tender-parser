from __future__ import annotations

from pathlib import Path

from tender_parser.notifications import NotificationConfig
from tender_parser.suppliers import SupplierIndexStatus
from tender_parser.telegram_bot import TelegramCommandBot


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
