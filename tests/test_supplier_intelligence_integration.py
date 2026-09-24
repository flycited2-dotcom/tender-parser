"""Offline Gmail-to-store integration checks for supplier synchronization."""

from __future__ import annotations

import base64
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tender_parser.supplier_intelligence.config import MailboxConfig, SupplierConfig
from tender_parser.supplier_intelligence.message_parser import normalize_gmail_message
from tender_parser.supplier_intelligence.storage import SupplierStore
from tender_parser.supplier_intelligence.sync import SupplierSync, _store_path


OWN = "termoark@gmail.com"
OTHER_OWN = "flycited@gmail.com"


def _raw(message_id: str, *, sender: str, recipient: str, subject: str, body: str, thread: str = "thread-1") -> dict:
    encoded = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    return {
        "id": message_id,
        "threadId": thread,
        "historyId": "105",
        "internalDate": "1790000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": recipient},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": encoded},
        },
    }


def _config(tmp_path: Path, *, max_retries: int = 1) -> SupplierConfig:
    return SupplierConfig(
        base_dir=tmp_path,
        database_path=tmp_path / "suppliers.sqlite",
        client_secret_path=tmp_path / "unused-client.json",
        service_account_path=tmp_path / "missing-service-account.json",
        spreadsheet_id="",
        mailboxes=(MailboxConfig(OWN, tmp_path / "unused-token.json"),),
        own_emails=frozenset({OWN, OTHER_OWN}),
        own_domains=frozenset({"simfer.com.ru"}),
        max_retries=max_retries,
    )


class FakeCollector:
    messages: dict[str, dict] = {}
    history_ids: list[str] = []
    gets: list[str] = []

    def __init__(self, account: str, client_secret: Path, token: Path) -> None:
        self.account = account

    def authorize(self, *, interactive: bool) -> None:
        assert interactive is False

    def list_message_ids(self, page_token=None, page_size=500):
        assert page_token is None
        return list(self.messages), None, "100"

    def list_history(self, start_history, page_token=None):
        assert page_token is None
        return list(self.history_ids), None, "200"

    def get_message(self, message_id: str) -> dict:
        self.gets.append(message_id)
        return self.messages[message_id]

    def get_attachment(self, message_id: str, attachment_id: str) -> dict:
        raise AssertionError("No attachment expected")

    def profile(self) -> dict:
        return {"historyId": "200"}


@pytest.fixture(autouse=True)
def _reset_fake_collector() -> None:
    FakeCollector.messages = {}
    FakeCollector.history_ids = []
    FakeCollector.gets = []


def test_outgoing_rfq_does_not_turn_own_signature_into_supplier_or_quote(tmp_path: Path) -> None:
    raw = _raw(
        "out-1", sender=OWN, recipient="ООО Альфа <sales@alpha.example>",
        subject="Запрос T26-0009-R06: медицинский холодильник POZIS ХФ-140-2",
        body=(
            "Просим КП на медицинский холодильник POZIS ХФ-140-2, 2 шт. Цена ориентира 21 000 ₽.\n\n"
            "С уважением,\nИван Иванов\nООО «Технолайн Трейд»\n"
            "+7 (999) 111-22-33\ntermoark@gmail.com\nsimfer.com.ru"
        ),
    )
    parsed = normalize_gmail_message(
        OWN, raw, own_emails={OWN, OTHER_OWN}, own_domains={"simfer.com.ru"}
    )
    assert parsed["direction"] == "OUT"
    assert parsed["supplier_email"] == "sales@alpha.example"
    assert parsed["supplier"]["company_name"] == "ООО Альфа"
    assert parsed["supplier"]["website"] != "simfer.com.ru"
    assert parsed["supplier"]["phone"] == ""
    assert parsed["signature"] == {}
    assert parsed["quote_lines"] == []
    assert parsed["products"]
    store = SupplierStore(tmp_path / "out.sqlite")
    assert store.ingest(parsed)["suppliers_created"] == 1
    rows = store.sheet_rows()
    assert rows["SUPPLIERS"][0]["COMPANY_NAME"] != "ООО «Технолайн Трейд»"
    assert not rows["CONTACTS"]
    assert not rows["QUOTES"]


def test_forwarded_supplier_mail_uses_original_sender_body_and_date() -> None:
    raw = _raw(
        "fwd-1", sender=OWN, recipient=OTHER_OWN,
        subject="Fwd: прайс от поставщика",
        body=(
            "Пересылаю предложение.\n\n---------- Forwarded message ---------\n"
            "From: Анна <sales@pozis-vendor.example>\n"
            "Date: Tue, 22 Sep 2026 10:00:00 +0300\n"
            "Subject: КП T26-0009-R06 на медицинский холодильник\n"
            "To: termoark@gmail.com\n\n"
            "Направляем КП на медицинский холодильник POZIS ХФ-140-2.\n"
            "Холодильник POZIS ХФ-140-2, 2 шт × 21 000 ₽ = 42 000 ₽ с НДС 20%\n\n"
            "С уважением,\nАнна Иванова\nООО «Поставщик»\n"
            "+7 (978) 123-45-67\nsales@pozis-vendor.example"
        ),
    )
    parsed = normalize_gmail_message(
        OTHER_OWN, raw, own_emails={OWN, OTHER_OWN}, own_domains={"simfer.com.ru"}
    )
    assert parsed["direction"] == "IN"
    assert parsed["from_email"] == "sales@pozis-vendor.example"
    assert parsed["supplier_email"] == "sales@pozis-vendor.example"
    assert parsed["supplier"]["company_name"] == "ООО «Поставщик»"
    assert parsed["signature"]["person"] == "Анна Иванова"
    assert parsed["tender_id"] == "T26-0009"
    assert parsed["rfq_id"] == "R06"
    assert parsed["quote_lines"][0]["price_unit"] == 21_000
    assert parsed["products"][0]["model"] == "ХФ-140-2"
    assert parsed["date"].startswith("2026-09-22T10:00:00+03:00")
    assert parsed["forwarded_original_subject"].startswith("КП T26-0009-R06")


def test_forwarded_outgoing_rfq_uses_original_recipient() -> None:
    raw = _raw(
        "fwd-out", sender=OWN, recipient=OTHER_OWN,
        subject="Fwd: наш запрос",
        body=(
            "Сохраняю копию.\n\n---------- Forwarded message ---------\n"
            "From: termoark@gmail.com\n"
            "Date: Tue, 22 Sep 2026 10:00:00 +0300\n"
            "Subject: Запрос T26-0009-R06 на огнетушители\n"
            "To: ООО Альфа <sales@alpha.example>\n\n"
            "Просим КП на огнетушители.\n\nС уважением,\n"
            "Иван Иванов\nООО «Технолайн Трейд»\ntermoark@gmail.com"
        ),
    )
    parsed = normalize_gmail_message(
        OTHER_OWN, raw, own_emails={OWN, OTHER_OWN}, own_domains={"simfer.com.ru"}
    )
    assert parsed["direction"] == "OUT"
    assert parsed["supplier_email"] == "sales@alpha.example"
    assert parsed["supplier"]["company_name"] == "ООО Альфа"
    assert parsed["signature"] == {}
    assert parsed["quote_lines"] == []


def test_fake_collector_backfill_incremental_idempotency_and_dry_run(tmp_path: Path) -> None:
    config = _config(tmp_path)
    FakeCollector.messages = {
        "m1": _raw(
            "m1", sender="Анна <anna@alpha.example>", recipient=OWN,
            subject="КП T26-0009-R06 на сервировочную тележку",
            body="Направляем КП.\nТележка сервировочная, 1 шт × 21 000 ₽ = 21 000 ₽\n\nС уважением,\nАнна Иванова\nООО Альфа\nanna@alpha.example",
        ),
    }
    sync = SupplierSync(config, collector_factory=FakeCollector)
    first = sync.run(backfill=True)
    assert first.totals()["messages_processed"] == 1
    store = SupplierStore(config.database_path)
    assert store.stats()["processed_messages"] == 1
    assert store.get_sync_state(OWN) == "200"

    FakeCollector.messages["m2"] = _raw(
        "m2", sender="Анна <anna@alpha.example>", recipient=OWN,
        subject="Ответ T26-0009-R06", body="Запрос получили, приняли в работу.",
    )
    FakeCollector.history_ids = ["m2"]
    second = sync.run()
    assert second.totals()["messages_processed"] == 1
    assert SupplierStore(config.database_path).stats()["processed_messages"] == 2
    FakeCollector.gets.clear()
    repeated = sync.run()
    assert repeated.totals()["messages_processed"] == 0
    assert FakeCollector.gets == []

    FakeCollector.messages["m3"] = _raw(
        "m3", sender="Пётр <petr@beta.example>", recipient=OWN,
        subject="КП на огнетушители", body="Предлагаем огнетушители.\nС уважением,\nПётр Петров\nООО Бета\npetr@beta.example",
        thread="thread-2",
    )
    FakeCollector.history_ids = ["m3"]
    before = SupplierStore(config.database_path).stats()
    dry = sync.run(dry_run=True)
    assert dry.totals()["messages_processed"] == 1
    assert dry.totals()["suppliers_created"] == 1
    assert SupplierStore(config.database_path).stats() == before
    assert not SupplierStore(config.database_path).seen(OWN, "m3")


def test_scheduled_sync_advances_both_mailboxes_before_either_backfill_finishes(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        mailboxes=(
            MailboxConfig(OWN, tmp_path / "first-token.json"),
            MailboxConfig(OTHER_OWN, tmp_path / "second-token.json"),
        ),
    )

    class TwoPageCollector:
        requested: list[tuple[str, str | None]] = []

        def __init__(self, account: str, client_secret: Path, token: Path) -> None:
            self.account = account

        def authorize(self, *, interactive: bool) -> None:
            assert interactive is False

        def list_message_ids(self, page_token=None, page_size=500):
            self.requested.append((self.account, page_token))
            suffix = "a" if self.account == OWN else "b"
            if page_token is None:
                return [f"{suffix}1"], "next", "100"
            assert page_token == "next"
            return [f"{suffix}2"], None, None

        def list_history(self, start_history, page_token=None):
            assert start_history == "100"
            return [], None, "200"

        def get_message(self, message_id: str) -> dict:
            sender = "sales@alpha.example" if self.account == OWN else "sales@beta.example"
            return _raw(
                message_id, sender=sender, recipient=self.account,
                subject="КП на огнетушители", body="Предлагаем огнетушители.",
                thread=f"thread-{message_id}",
            )

        def get_attachment(self, message_id: str, attachment_id: str) -> dict:
            raise AssertionError("No attachment expected")

        def profile(self) -> dict:
            return {"historyId": "200"}

    sync = SupplierSync(config, collector_factory=TwoPageCollector)
    first = sync.run()
    store = SupplierStore(config.database_path)
    assert TwoPageCollector.requested == [(OWN, None), (OTHER_OWN, None)]
    assert first.totals()["messages_processed"] == 2
    assert first.mailboxes[OWN]["status"] == "BACKFILLING"
    assert first.mailboxes[OTHER_OWN]["status"] == "BACKFILLING"
    assert store.get_checkpoint(OWN) == "next"
    assert store.get_checkpoint(OTHER_OWN) == "next"
    assert store.get_backfill_start_history(OWN) == "100"
    assert store.get_backfill_start_history(OTHER_OWN) == "100"
    assert not store.get_sync_state(OWN)
    assert not store.get_sync_state(OTHER_OWN)

    second = sync.run()
    assert TwoPageCollector.requested[-2:] == [(OWN, "next"), (OTHER_OWN, "next")]
    assert second.totals()["messages_processed"] == 2
    assert second.mailboxes[OWN]["status"] == "OK"
    assert second.mailboxes[OTHER_OWN]["status"] == "OK"
    assert store.get_checkpoint(OWN) is None
    assert store.get_checkpoint(OTHER_OWN) is None
    assert store.get_sync_state(OWN) == "200"
    assert store.get_sync_state(OTHER_OWN) == "200"
    assert store.stats()["processed_messages"] == 4


def test_one_bad_message_is_retried_without_stopping_other_mail(tmp_path: Path) -> None:
    config = _config(tmp_path)
    FakeCollector.messages = {
        "bad": {"id": "bad"},
        "good": _raw(
            "good", sender="sales@alpha.example", recipient=OWN,
            subject="КП на огнетушители", body="Предлагаем огнетушители. ООО Альфа",
        ),
    }
    sync = SupplierSync(config, collector_factory=FakeCollector)
    first = sync.run(backfill=True)
    assert first.totals()["messages_processed"] == 1
    assert first.totals()["error_count"] == 1
    store = SupplierStore(config.database_path)
    assert store.seen(OWN, "good")
    assert store.pending_errors(OWN) == ["bad"]

    FakeCollector.messages["bad"] = _raw(
        "bad", sender="sales@beta.example", recipient=OWN,
        subject="КП на стабилизаторы напряжения", body="Предлагаем стабилизаторы напряжения. ООО Бета",
        thread="thread-2",
    )
    FakeCollector.history_ids = []
    second = sync.run()
    assert second.totals()["messages_processed"] == 1
    assert SupplierStore(config.database_path).pending_errors(OWN) == []
    assert SupplierStore(config.database_path).stats()["processed_messages"] == 2


def test_dry_run_closes_backup_sqlite_connections_before_deleting_temp(tmp_path: Path, monkeypatch) -> None:
    from tender_parser.supplier_intelligence import sync as sync_module

    original = tmp_path / "original.sqlite"
    sqlite3.connect(original).close()
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def tracked_connect(path, *args, **kwargs):
        connection = real_connect(path, *args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sync_module.sqlite3, "connect", tracked_connect)
    with _store_path(original, True) as copied:
        assert copied.exists()
    assert not copied.parent.exists()
    assert len(opened) == 2
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
