from datetime import datetime
from pathlib import Path

import requests

from tender_parser.models import TenderRecord
from tender_parser.notifications import (
    NotificationConfig,
    TelegramNotifier,
    build_daily_run_summary,
    build_notification_digest,
    export_notification_digest,
)


def make_tender(index: int = 1) -> TenderRecord:
    return TenderRecord(
        title=f"Поставка МФУ №{index}",
        url=f"https://example.test/tender-{index}",
        source="eis-zakupki",
        tender_number=str(index),
        region="Республика Крым",
        price=125_500.0,
        deadline=datetime(2026, 8, 20, 15, 30),
        review_priority="hot",
    )


def test_notification_config_requires_token_and_chat_id(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert NotificationConfig.from_env().enabled is False

    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    config = NotificationConfig.from_env()
    assert config.enabled is True
    assert config.max_items == 10


def test_build_notification_digest_contains_useful_fields_and_limits_items() -> None:
    digest = build_notification_digest([make_tender(1), make_tender(2)], max_items=1)

    assert "Новых подходящих закупок: 2" in digest
    assert "Поставка МФУ №1" in digest
    assert "125 500 ₽" in digest
    assert "20.08.2026 15:30" in digest
    assert "Ещё закупок: 1" in digest
    assert "Поставка МФУ №2" not in digest


def test_export_notification_digest_writes_even_when_no_new_tenders(tmp_path: Path) -> None:
    output = export_notification_digest([], tmp_path / "notification.txt")

    assert output.read_text(encoding="utf-8") == "Новых подходящих закупок нет.\n"


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class FakeSession:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()
        self.calls: list[tuple[str, dict[str, object], int]] = []

    def post(self, url: str, *, json: dict[str, object], timeout: int) -> FakeResponse:
        self.calls.append((url, json, timeout))
        return self.response


def test_telegram_notifier_sends_plain_digest_without_exposing_token() -> None:
    session = FakeSession()
    config = NotificationConfig(bot_token="secret-token", chat_id="123", max_items=5)

    result = TelegramNotifier(config, session=session).notify([make_tender()])

    assert result.status == "sent"
    assert result.sent_count == 1
    url, payload, timeout = session.calls[0]
    assert url.endswith("/botsecret-token/sendMessage")
    assert payload["chat_id"] == "123"
    assert "Поставка МФУ" in str(payload["text"])
    assert timeout == 10
    assert "secret-token" not in result.detail


def test_telegram_notifier_returns_sanitized_error() -> None:
    session = FakeSession(FakeResponse(401))
    config = NotificationConfig(bot_token="secret-token", chat_id="123")

    result = TelegramNotifier(config, session=session).notify([make_tender()])

    assert result.status == "error"
    assert result.sent_count == 0
    assert "secret-token" not in result.detail
    assert "HTTPError" in result.detail


def test_daily_summary_and_google_button_are_sent() -> None:
    session = FakeSession()
    config = NotificationConfig(
        bot_token="secret-token",
        chat_id="123",
        spreadsheet_url="https://docs.google.com/spreadsheets/d/test/edit",
    )
    summary = build_daily_run_summary(
        active_count=42,
        new_count=3,
        source_ok=9,
        source_total=10,
        google_status="synced",
    )

    result = TelegramNotifier(config, session=session).send_text(summary, buttons=True)

    assert result.status == "sent"
    payload = session.calls[0][1]
    assert "Новых закупок: 3" in str(payload["text"])
    assert "Открыть Google-таблицу" in str(payload["reply_markup"])
