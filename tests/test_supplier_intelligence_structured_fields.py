"""Explicit supplier, contact and refusal facts from newly authored mail."""

from __future__ import annotations

import base64

from tender_parser.supplier_intelligence.message_parser import normalize_gmail_message
from tender_parser.supplier_intelligence.signature_parser import parse_signature
from tender_parser.supplier_intelligence.storage import SupplierStore
from tender_parser.supplier_intelligence.structured_fields import refusal_reason, supplier_role


OWN = "termoark@gmail.com"


def _raw(message_id: str, body: str, *, sender: str = "Анна Иванова <anna@vendor.example>",
         recipient: str = OWN, subject: str = "Ответ по запросу", thread: str = "thread-1") -> dict:
    encoded = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    return {
        "id": message_id, "threadId": thread, "historyId": "123", "internalDate": "1790000000000",
        "payload": {"mimeType": "text/plain", "headers": [
            {"name": "From", "value": sender},
            {"name": "To", "value": recipient},
            {"name": "Subject", "value": subject},
        ], "body": {"data": encoded}},
    }


def _normalize(raw: dict) -> dict:
    return normalize_gmail_message(OWN, raw, own_emails={OWN}, own_domains=set())


def test_signature_extracts_explicit_contact_and_location_fields() -> None:
    signature = parse_signature(
        "С уважением,\nАнна Иванова\nООО «Поставщик»\n"
        "+7 (978) 123-45-67 доб. 204\n"
        "Telegram: @anna_vendor\nWhatsApp: +7 (978) 123-45-67\n"
        "Регион: Московская область\nЮр. адрес: г. Химки, ул. Ленина, д. 7\n"
        "anna@vendor.example"
    )
    assert signature["phone_ext"] == "204"
    assert signature["telegram"] == "@anna_vendor"
    assert signature["whatsapp"] == "+79781234567"
    assert signature["region"] == "Московская область"
    assert signature["address"] == "г. Химки, ул. Ленина, д. 7"


def test_role_requires_first_party_claim() -> None:
    assert supplier_role("Производитель холодильника POZIS указан в паспорте.", "")["supplier_type"] == ""
    assert supplier_role("Мы официальный дистрибьютор POZIS.", "")["supplier_type"] == "OFFICIAL_DISTRIBUTOR"
    assert supplier_role("", "Официальный дилер POZIS")["dealer"] == "Да"


def test_refusal_reason_matches_explicit_business_reasons() -> None:
    assert refusal_reason("Нет нужной модели в наличии.") == ("Нет нужной модели", "NO_STOCK")
    assert refusal_reason("Не участвуем в тендерах.") == ("Не участвуем в тендерах", "REFUSAL")
    assert refusal_reason("Проект зарегистрирован на другого дилера.") == (
        "Проект зарегистрирован на другого дилера", "REFUSAL"
    )
    assert refusal_reason("Подскажите, есть ли товар в наличии?") == ("", "")


def test_message_type_and_refusal_do_not_use_quoted_history() -> None:
    out = _normalize(_raw(
        "out", "Просим предоставить цену на холодильник POZIS ХФ-140-2.",
        sender=OWN, recipient="sales@vendor.example", subject="Запрос цены",
    ))
    assert out["message_type"] == "RFQ"
    assert out["refusal_reason"] == ""
    inbound = _normalize(_raw(
        "in", "Мы не участвуем в тендерах.\n"
        "On Tue, 22 Sep 2026 Buyer wrote:\n> Мы официальный производитель.",
    ))
    assert inbound["response_type"] == "REFUSAL"
    assert inbound["message_type"] == "REFUSAL"
    assert inbound["refusal_reason"] == "Не участвуем в тендерах"
    assert inbound["supplier"]["supplier_type"] == ""


def test_existing_contact_is_enriched_from_later_signature(tmp_path) -> None:
    store = SupplierStore(tmp_path / "suppliers.sqlite")
    first = _normalize(_raw(
        "m1", "Направляем КП на медицинский холодильник.\n\n"
        "С уважением,\nАнна Иванова\nООО «Поставщик»\n"
        "+7 (978) 123-45-67\nanna@vendor.example",
    ))
    second = _normalize(_raw(
        "m2", "Мы официальный дистрибьютор POZIS. Нет нужной модели.\n\n"
        "С уважением,\nАнна Иванова\nООО «Поставщик»\n"
        "+7 (978) 123-45-67 доб. 204\n"
        "Telegram: @anna_vendor\nWhatsApp: +7 (978) 123-45-67\n"
        "Регион: Московская область\nЮр. адрес: г. Химки, ул. Ленина, д. 7\n"
        "anna@vendor.example",
    ))
    assert store.ingest(first)["status"] == "processed"
    assert store.ingest(second)["status"] == "processed"
    rows = store.sheet_rows()
    assert len(rows["SUPPLIERS"]) == 1
    assert len(rows["CONTACTS"]) == 1
    supplier = rows["SUPPLIERS"][0]
    contact = rows["CONTACTS"][0]
    interaction = next(item for item in rows["INTERACTIONS"] if item["MESSAGE_ID"] == "m2")
    assert supplier["SUPPLIER_TYPE"] == "OFFICIAL_DISTRIBUTOR"
    assert supplier["DISTRIBUTOR"] == "Да"
    assert supplier["REGION"] == "Московская область"
    assert supplier["ADDRESS"] == "г. Химки, ул. Ленина, д. 7"
    assert contact["PHONE_EXT"] == "204"
    assert contact["TELEGRAM"] == "@anna_vendor"
    assert contact["WHATSAPP"] == "+79781234567"
    assert interaction["RESPONSE_TYPE"] == "NO_STOCK"
    assert interaction["REFUSAL_REASON"] == "Нет нужной модели"
