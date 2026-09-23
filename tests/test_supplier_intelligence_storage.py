from __future__ import annotations

from pathlib import Path

from tender_parser.supplier_intelligence.sheet_schema import HEADERS
from tender_parser.supplier_intelligence.storage import SupplierStore


def message(message_id: str, *, mailbox: str = "termoark@gmail.com", **changes: object) -> dict:
    value = {
        "gmail_account": mailbox,
        "message_id": message_id,
        "thread_id": "thread-1",
        "history_id": "100",
        "date": "2026-09-01T10:00:00+00:00",
        "direction": "IN",
        "from_email": "anna@company.example",
        "to_emails": [mailbox],
        "cc_emails": [],
        "reply_to": "",
        "subject": "КП на сервировочные тележки T26-0009-R06",
        "new_message_body": "Предлагаем тележку 1000 руб.",
        "quoted_history": "",
        "signature": {"person": "Анна Иванова", "company": "ООО Компания", "phones": ["+7 999 123-45-67"]},
        "supplier": {"company_name": "ООО Компания", "inn": "1234567890", "email": "anna@company.example"},
        "tender_id": "T26-0009",
        "rfq_id": "R06",
        "categories": [{"category_l1": "HoReCa", "category_l2": "Оборудование", "category_l3": "Сервировочные тележки"}],
        "products": [{"product_name": "Сервировочная тележка МСК-653.12", "model": "МСК-653.12", "category_l1": "HoReCa"}],
        "response_type": "COMMERCIAL_OFFER",
        "quote_lines": [{"product_name": "Сервировочная тележка МСК-653.12", "price_unit": 1000, "currency": "RUB"}],
        "attachments": [{"attachment_id": "att-1", "filename": "offer.pdf", "mime_type": "application/pdf", "file_size": 100}],
        "supplier_signal": True,
    }
    value.update(changes)
    return value


def test_idempotent_multi_mailbox_merge_and_history(tmp_path: Path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    first = store.ingest(message("m1"))
    assert first["supplier_id"] == "SUP-000001"
    assert first["quotes_created"] == 1
    assert store.ingest(message("m1"))["status"] == "duplicate"

    second = message(
        "m2", mailbox="flycited@gmail.com", thread_id="thread-2",
        from_email="petr@company.example", date="2026-09-15T10:00:00+00:00",
        signature={"person": "Пётр Петров", "company": "Компания"},
        supplier={"company_name": "Компания", "email": "petr@company.example"},
        quote_lines=[{"product_name": "Сервировочная тележка МСК-653.12", "price_unit": 1200, "currency": "RUB"}],
    )
    assert store.ingest(second)["supplier_id"] == "SUP-000001"
    stats = store.stats()
    assert (stats["suppliers"], stats["contacts"], stats["quotes"], stats["processed_messages"]) == (1, 2, 2, 2)
    rows = store.sheet_rows()
    assert all(set(item) == set(HEADERS[name]) for name in HEADERS for item in rows[name])
    supplier = rows["SUPPLIERS"][0]
    assert supplier["SOURCE_MAILBOXES"] in {
        "termoark@gmail.com,flycited@gmail.com",
        "flycited@gmail.com,termoark@gmail.com",
    }
    assert supplier["TOTAL_QUOTES"] == 2
    assert {q["PRICE_UNIT"] for q in rows["QUOTES"]} == {"1000", "1200"}
    assert store.find_suppliers("сервировочные тележки")[0]["supplier_id"] == "SUP-000001"
    assert store.suggest_suppliers_for_rfq(product="МСК-653.12")[0]["supplier_id"] == "SUP-000001"


def test_public_mail_domains_do_not_merge_and_conflicting_inn_is_reviewed(tmp_path: Path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    one = message("one", thread_id="first-thread", from_email="first@gmail.com", supplier={"company_name": "Альфа", "email": "first@gmail.com", "inn": "1234567890"}, signature={})
    two = message("two", thread_id="second-thread", from_email="second@gmail.com", supplier={"company_name": "Бета", "email": "second@gmail.com", "inn": "9876543210"}, signature={})
    assert store.ingest(one)["supplier_id"] == "SUP-000001"
    assert store.ingest(two)["supplier_id"] == "SUP-000002"
    assert store.stats()["suppliers"] == 2

    conflict = message("three", thread_id="third-thread", from_email="new@company.example", supplier={"company_name": "Альфа", "email": "new@company.example", "inn": "9999999999"}, signature={})
    # Company name matches a different INN. The system must not attach it silently.
    result = store.ingest(conflict)
    assert result["status"] == "review"
    assert result["supplier_id"] == ""
    assert store.stats()["review_queue"] >= 1


def test_thread_context_inherits_ids_but_not_quoted_products(tmp_path: Path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    outgoing = message(
        "out", direction="OUT", from_email="termoark@gmail.com", to_emails=["sales@company.example"],
        supplier={"company_name": "Компания", "email": "sales@company.example"}, signature={},
        quote_lines=[], response_type="", date="2026-09-01T10:00:00+00:00",
    )
    assert store.ingest(outgoing)["supplier_id"] == "SUP-000001"
    reply = message(
        "reply", direction="IN", from_email="", to_emails=["termoark@gmail.com"],
        supplier={}, signature={}, subject="Ответ", tender_id="", rfq_id="",
        categories=[], products=[], quote_lines=[], attachments=[],
        new_message_body="Запрос получили", quoted_history="Сервировочная тележка МСК-653.12",
        response_type="ACKNOWLEDGEMENT",
    )
    assert store.ingest(reply)["supplier_id"] == "SUP-000001"
    rows = store.sheet_rows()
    assert len(rows["SUPPLIER_PRODUCTS"]) == 1
    assert rows["INTERACTIONS"][-1]["RFQ_ID"] == "R06"
    assert rows["INTERACTIONS"][-1]["TENDER_ID"] == "T26-0009"
    assert rows["SUPPLIERS"][0]["RFQ_REPLIED"] == 1


def test_force_reprocess_replaces_only_one_message_and_checkpoint(tmp_path: Path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    original = message("m1")
    store.ingest(original)
    store.ingest(message("m2", thread_id="thread-2", quote_lines=[{"product_name": "Тележка", "price_unit": 2000}]))
    replacement = message("m1", quote_lines=[{"product_name": "Тележка", "price_unit": 1300}], products=[])
    assert store.ingest(replacement, force=True)["error_count"] == 0
    rows = store.sheet_rows()
    assert len(rows["QUOTES"]) == 2
    assert {row["PRICE_UNIT"] for row in rows["QUOTES"]} == {"1300", "2000"}
    assert len(rows["INTERACTIONS"]) == 2
    assert len(rows["SUPPLIER_PRODUCTS"]) == 1
    assert rows["SUPPLIER_PRODUCTS"][0]["SOURCE_MESSAGE_ID"] == "m2"

    store.set_checkpoint("termoark@gmail.com", "page-2")
    store.set_backfill_start_history("termoark@gmail.com", "99")
    assert store.get_checkpoint("termoark@gmail.com") == "page-2"
    assert store.get_backfill_start_history("termoark@gmail.com") == "99"
    store.set_checkpoint("termoark@gmail.com", None)
    assert store.get_checkpoint("termoark@gmail.com") is None
    store.set_sync_state("termoark@gmail.com", "101")
    assert store.get_sync_state("termoark@gmail.com") == "101"


def test_pending_error_retry_and_run_log(tmp_path: Path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    store.record_error("termoark@gmail.com", "missing", "temporary Gmail failure")
    store.record_error("termoark@gmail.com", "missing", "temporary Gmail failure")
    assert store.pending_errors("termoark@gmail.com") == ["missing"]
    store.clear_error("termoark@gmail.com", "missing")
    assert store.pending_errors("termoark@gmail.com") == []
    run_id = store.record_run({"mailbox": "termoark@gmail.com", "status": "DONE", "messages_found": 2})
    assert run_id.startswith("RUN-")
    assert store.sheet_rows()["PROCESSING_LOG"][-1]["MESSAGES_FOUND"] == 2


def test_outgoing_multiple_recipients_get_distinct_supplier_facts(tmp_path: Path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    outgoing = message(
        "mass-rfq", direction="OUT", from_email="termoark@gmail.com",
        to_emails=["sales@alpha.example", "sales@beta.example", "flycited@gmail.com"],
        cc_emails=["manager@alpha.example"],
        supplier={"company_name": "Альфа", "email": "sales@alpha.example"}, signature={},
        quote_lines=[], attachments=[], response_type="", date="2026-09-01T10:00:00+00:00",
    )
    result = store.ingest(outgoing)
    assert result["error_count"] == 0
    assert result["suppliers_created"] == 2
    assert set(result["supplier_ids"]) == {"SUP-000001", "SUP-000002"}
    rows = store.sheet_rows()
    assert len(rows["INTERACTIONS"]) == 2
    assert {row["TO_EMAIL"] for row in rows["INTERACTIONS"]} == {
        "manager@alpha.example, sales@alpha.example", "sales@beta.example"
    }
    assert len(rows["SUPPLIERS"]) == 2
    assert next(row for row in rows["SUPPLIERS"] if row["DOMAIN"] == "beta.example")["COMPANY_NAME"] != "Альфа"
    assert store.seen("termoark@gmail.com", "mass-rfq")

    reply = message(
        "beta-reply", direction="IN", from_email="sales@beta.example",
        to_emails=["termoark@gmail.com"], supplier={"email": "sales@beta.example"},
        signature={}, subject="Ответ", tender_id="", rfq_id="", categories=[], products=[],
        quote_lines=[], attachments=[], response_type="ACKNOWLEDGEMENT",
    )
    assert store.ingest(reply)["supplier_id"] == "SUP-000002"
    assert store.sheet_rows()["INTERACTIONS"][-1]["RFQ_ID"] == "R06"


def test_low_confidence_category_is_reviewed_before_it_becomes_a_search_fact(tmp_path: Path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    weak = message(
        "weak", categories=[{"category_l1": "HoReCa", "confidence": 0.55}],
        products=[{"product_name": "Сомнительный товар", "confidence": 0.55}],
        quote_lines=[],
    )
    result = store.ingest(weak)
    assert result["review_items"] == 2
    assert store.stats()["categories"] == 0
    assert store.stats()["products"] == 0
    assert not store.suggest_suppliers_for_rfq(category="HoReCa")


def test_ambiguous_price_goes_to_review_without_creating_a_quote(tmp_path: Path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    result = store.ingest(message(
        "ambiguous-price", quote_lines=[{
            "product_name": "Холодильник POZIS ХФ-140-2",
            "unresolved_price": 21000,
            "currency": "RUB",
        }],
    ))
    assert result["status"] == "processed"
    assert result["quotes_created"] == 0
    assert store.stats()["quotes"] == 0
    assert store.sheet_rows()["REVIEW_QUEUE"][-1]["TYPE"] == "QUOTE_PARSE_ERROR"
