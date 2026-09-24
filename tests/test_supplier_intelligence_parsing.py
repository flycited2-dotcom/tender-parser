"""Evidence-focused tests for supplier mail parsing and normalization."""

from __future__ import annotations

import json

from tender_parser.supplier_intelligence import (
    classify_products,
    classify_response,
    extract_ids,
    extract_quote_lines,
    is_public_email_domain,
    normalize_company,
    normalize_email,
    normalize_phone,
    parse_signature,
    split_quoted,
)
from tender_parser.supplier_intelligence.resolver import normalize_domain


def test_normalization_and_public_domains() -> None:
    assert normalize_email(" MAILTO:Sales@Company.RU ") == "sales@company.ru"
    assert normalize_email("Иван <IVAN@COMPANY.RU>") == "ivan@company.ru"
    assert normalize_email("not-an-email") == ""
    assert normalize_phone("+7 (978) 123-45-67") == "+79781234567"
    assert normalize_phone("8 978 123 45 67 доб. 123") == "+79781234567"
    assert normalize_phone("+79781234567") == "+79781234567"
    assert normalize_phone("12345") == ""
    assert normalize_company('ООО «ПРОЛАЙН»') == normalize_company("Пролайн")
    assert is_public_email_domain("someone@gmail.com")
    assert is_public_email_domain("MAIL.RU")
    assert not is_public_email_domain("sales@company.ru")


def test_extract_tender_and_rfq_ids_from_subject_and_body() -> None:
    assert extract_ids("Re: КП T26-0009-R06", "другой T25-0001-R01") == {
        "tender_id": "T26-0009",
        "rfq_id": "R06",
    }
    assert extract_ids("Без номера", "Запрос T26-0009, этап R06") == {
        "tender_id": "T26-0009",
        "rfq_id": "R06",
    }
    assert extract_ids("Предложение") == {"tender_id": None, "rfq_id": None}


def test_quoted_history_and_signature_are_separate() -> None:
    mail = (
        "Направляем КП на холодильник POZIS ХФ-140-2.\n\n"
        "С уважением,\n"
        "Иванова Анна Сергеевна\n"
        "Менеджер отдела продаж\n"
        "ООО «Компания»\n"
        "+7 (999) 123-45-67\n"
        "sales@company.ru\n"
        "company.ru\n\n"
        "On Tue, 22 Sep 2026 10:00:00 +0300 Buyer wrote:\n"
        "> Просим дать цену на POZIS ХФ-250-4.\n"
    )
    split = split_quoted(mail)
    assert "ХФ-140-2" in split["new_message_body"]
    assert "ХФ-250-4" not in split["new_message_body"]
    assert "ХФ-250-4" in split["quoted_history"]
    assert "Иванова" in split["signature"]
    signature = parse_signature(mail)
    assert signature["person"] == "Иванова Анна Сергеевна"
    assert signature["position"] == "Менеджер отдела продаж"
    assert signature["company"] == "ООО «Компания»"
    assert signature["phones"] == ["+79991234567"]
    assert signature["emails"] == ["sales@company.ru"]
    assert signature["website"] == "company.ru"


def test_person_initials_are_not_mistaken_for_a_website() -> None:
    signature = parse_signature(
        "С уважением,\nИП Никонова А.Д.\n+7 (978) 172-73-70\ninfo@nproline.ru"
    )
    assert signature["website"] == ""
    assert normalize_domain("а.д") == ""


def test_signature_website_ignores_email_handles_and_social_links() -> None:
    signature = parse_signature(
        "С уважением,\nМенеджер\nООО «ХКА»8-800-7000-100holdcable.com*\n"
        "alexandr.grebnev@mics.ru\nhttps://vk.com/holdcable"
    )
    assert signature["company"] == "ООО «ХКА»"
    assert signature["website"] == "holdcable.com"
    assert parse_signature("С уважением,\nМенеджер\ntimina.ta@a1tis.ru\nЯндекс.Почты")["website"] == ""


def test_response_classification_uses_new_message_only() -> None:
    assert classify_response("К сожалению, товара нет в наличии") == "NO_STOCK"
    assert classify_response("Не участвуем в тендерах") == "REFUSAL"
    assert classify_response("Направляем коммерческое предложение во вложении") == "COMMERCIAL_OFFER"
    assert classify_response("Во вложении прайс-лист") == "PRICE"
    assert classify_response("Направляем счёт на оплату") == "INVOICE"
    assert classify_response("Уточните, пожалуйста, количество") == "CLARIFICATION"
    assert classify_response("Запрос получили, приняли в работу") == "ACKNOWLEDGEMENT"
    assert classify_response("Спасибо, уточним.\nOn Tue, 22 Sep 2026 Buyer wrote:\n> Направляем КП") == "OTHER"


def test_classify_products_preserves_model_and_category_depth() -> None:
    rows = classify_products(
        "Запрос T26-0009-R06: POZIS ХФ-140-2",
        "Медицинский холодильник POZIS ХФ-140-2, 2 шт.",
        ["КП_POZIS_ХФ-140-2.pdf"],
    )
    assert len(rows) == 1
    product = rows[0]
    assert product["category_l1"] == "Медицинское оборудование"
    assert product["category_l2"] == "Медицинские холодильники"
    assert product["category_l3"] == "POZIS"
    assert product["category_l4"] == "ХФ-140-2"
    assert product["brand"] == "POZIS"
    assert product["model"] == "ХФ-140-2"
    assert product["manufacturer"] is None
    assert product["confidence"] >= 0.8


def test_requested_major_filter_categories_are_classified() -> None:
    examples = (
        ("Гидроизоляционная плёнка для кровли", "Строительство"),
        ("Печатная плата управления", "Электроника"),
        ("Лекарственные препараты", "Медпрепараты"),
        ("Сплит-система 2 шт.", "Климатическое оборудование"),
        ("Медицинский холодильник", "Медицинское оборудование"),
    )
    for subject, expected in examples:
        assert any(item["category_l1"] == expected for item in classify_products(subject, ""))


def test_procurement_vocabulary_covers_existing_mail_and_adjacent_groups() -> None:
    examples = (
        ("Полипропиленовые мешки 50 кг", "Упаковка"),
        ("Гибридный солнечный инвертор", "Солнечная энергетика"),
        ("Раскладные кровати с матрасами", "Мебель"),
        ("Кабельно-проводниковая продукция", "Электротехника"),
        ("МФУ лазерное", "ИТ и оргтехника"),
        ("Шприцы одноразовые", "Медицинские расходные материалы"),
        ("Лабораторная центрифуга", "Лабораторное оборудование"),
        ("Пароконвектомат", "HoReCa"),
        ("Пожарная сигнализация", "Пожарное оборудование"),
        ("Автомобильные шины", "Автотранспорт"),
        ("Уборочный инвентарь", "Хозтовары и уборка"),
        ("Канцтовары", "Канцелярия и бумага"),
    )
    for subject, expected in examples:
        rows = classify_products(subject, "")
        assert any(row["category_l1"] == expected for row in rows), subject


def test_medical_ventilation_is_not_climate_equipment() -> None:
    rows = classify_products("Аппарат ИВЛ для искусственной вентиляции легких", "")
    assert any(row["category_l1"] == "Медицинское оборудование" for row in rows)
    assert all(row["category_l1"] != "Климатическое оборудование" for row in rows)


def test_product_category_not_inferred_from_quoted_history_or_signature() -> None:
    assert classify_products("Ответ", "Спасибо.\nOn Tue, 22 Sep 2026 Buyer wrote:\n> Огнетушители") == []
    assert classify_products("Ответ", "Спасибо.\nС уважением,\nИван Иванов\nООО Огнетушители\nivan@company.ru") == []
    products = classify_products("Запрос сервировочных тележек", "Просим предложение.")
    assert any(item["category_l3"] == "Сервировочные тележки" for item in products)


def test_taxonomy_can_be_extended_without_code_change(tmp_path) -> None:
    taxonomy = tmp_path / "product_categories.yaml"
    taxonomy.write_text(
        json.dumps({"categories": [{"path": ["Лаборатория", "Микроскопы"], "terms": ["микроскоп*"], "brands": [], "model_patterns": []}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    rows = classify_products("Запрос микроскопа", "", config_path=taxonomy)
    assert [(item["category_l1"], item["category_l2"]) for item in rows] == [("Лаборатория", "Микроскопы")]


def test_quote_lines_only_assign_explicit_values_and_keep_price_history() -> None:
    old = extract_quote_lines("Холодильник POZIS ХФ-140-2, 2 шт × 21 000 ₽ = 42 000 ₽ с НДС 20%")
    new = extract_quote_lines("Холодильник POZIS ХФ-140-2, 2 шт × 24 000 ₽ = 48 000 ₽ с НДС 20%")
    assert old[0]["quantity"] == 2
    assert old[0]["price_unit"] == 21_000
    assert old[0]["price_total"] == 42_000
    assert old[0]["vat_rate"] == 20
    assert new[0]["price_unit"] == 24_000
    assert old[0]["price_unit"] == 21_000
    ambiguous = extract_quote_lines("Холодильник POZIS ХФ-140-2 21 000 ₽")
    assert ambiguous[0]["price_unit"] is None
    assert ambiguous[0]["price_total"] is None
    assert ambiguous[0]["unresolved_price"] == 21_000
    assert extract_quote_lines("Скидка на всю линейку") == []


def test_quote_lines_reject_boilerplate_totals_and_unrelated_prices() -> None:
    assert extract_quote_lines("Всего наименований: 1, на сумму 269 360 руб 48 573,11 руб") == []
    assert extract_quote_lines("Проверяйте заказ перед отправкой: возврат каждой позиции стоимостью более 6000 руб стоит 250 руб") == []
    assert extract_quote_lines("[FREE DOMESTIC SHIPPING ON ORDERS OVER $200 AND $300]") == []
    assert extract_quote_lines("Подтвердите, пожалуйста, актуальность цены 21 276 руб") == []
    assert extract_quote_lines("Цена за 1 шт 21 276 руб 170 208 руб") == []


def test_quote_lines_require_arithmetic_for_unlabelled_unit_and_total() -> None:
    valid = extract_quote_lines("Тележка сервировочная МСК-653.12, 8 шт × 4 000 руб = 32 000 руб")
    assert valid[0]["price_unit"] == 4000
    assert valid[0]["price_total"] == 32000
    ambiguous = extract_quote_lines("ПРОКАТ КРУГЛОГО КОНЬКА 100 руб 150 руб")
    assert ambiguous[0]["price_unit"] is None
    assert ambiguous[0]["price_total"] is None
    assert ambiguous[0]["unresolved_price"] == 100
    inconsistent = extract_quote_lines("Тележка сервировочная, 8 шт 4 000 руб 6 000 руб")
    assert inconsistent[0]["price_unit"] is None
    assert inconsistent[0]["price_total"] is None
