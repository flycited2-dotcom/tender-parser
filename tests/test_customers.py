from datetime import datetime

from tender_parser.customers import (
    build_customer_registry,
    compact_tender_region,
    customer_region,
    is_potential_customer,
    is_public_customer,
    organization_type,
)
from tender_parser.models import TenderRecord


def tender(**kwargs) -> TenderRecord:
    values = {
        "title": "Поставка оргтехники",
        "url": "https://example.test/tender/1",
        "source": "fake",
        "customer": 'ГБУ РК "Центр"',
        "region": "Республика Крым",
        "discovered_at": datetime(2026, 8, 13),
    }
    values.update(kwargs)
    return TenderRecord(**values)


def test_public_customer_detection_and_type() -> None:
    assert is_public_customer('МКУ "Департамент закупок"')
    assert is_public_customer('ФГБОУ "Университет"')
    assert not is_public_customer('ООО "Частный поставщик"')
    assert not is_public_customer('АНО "Автономная некоммерческая организация"')
    assert is_potential_customer('ООО "Частный поставщик"')
    assert is_potential_customer('АНО "Центр развития"')
    assert not is_potential_customer("Заказчик")
    assert not is_potential_customer("Республика Крым")
    assert not is_potential_customer("Подробнее")
    assert organization_type('ООО "Частный поставщик"') == "Коммерческая организация — ООО"
    assert organization_type('АО "Завод"') == "Коммерческая организация — АО"
    assert organization_type('АНО "Центр"') == "Некоммерческая организация — АНО"
    assert organization_type('ФГБОУ "Университет"') == "Образовательное учреждение"


def test_state_organization_is_not_misclassified_as_court() -> None:
    assert (
        organization_type("ГОСУДАРСТВЕННОЕ АВТОНОМНОЕ УЧРЕЖДЕНИЕ РЕСПУБЛИКИ КРЫМ")
        == "Автономное учреждение"
    )
    assert organization_type("ВЕРХОВНЫЙ СУД РЕСПУБЛИКИ КРЫМ") == "Суд"


def test_customer_region_requires_explicit_region_evidence() -> None:
    false_positive = tender(
        customer='ГАУ Амурской области "Дом"',
        region="Амурская область",
        include_reason="регион: Крым; ключевые слова: шкаф",
    )
    assert customer_region(false_positive) == ""


def test_registry_preserves_manually_verified_contacts() -> None:
    item = tender(customer='ГБУ РК "Центр"')
    key = "гбуркцентр"
    existing = [[key, 'ГБУ РК "Центр"', "", "", "9100000000", "Адрес", "", "mail@example.ru", "+7", "", "https://org.test", "https://org.test/contacts", "", "13.08.2026", "Проверен", "Не дублировать"]]

    rows = build_customer_registry([item], existing)

    assert len(rows) == 1
    assert rows[0][3] == "Республика Крым"
    assert rows[0][4:12] == existing[0][4:12]
    assert rows[0][12] == item.url
    assert rows[0][14:] == ["Проверен", "Не дублировать"]


def test_registry_keeps_commercial_but_skips_wrong_region_customers() -> None:
    rows = build_customer_registry(
        [
            tender(customer='ООО "Частное"'),
            tender(customer='ГБУ "Чужой регион"', region="Амурская область"),
        ],
        [],
    )
    assert len(rows) == 1
    assert rows[0][1] == 'ООО "Частное"'
    assert rows[0][2] == "Коммерческая организация — ООО"


def test_compact_region_replaces_long_multiregion_list_with_targets() -> None:
    item = tender(region="Республика Адыгея, Амурская область, Республика Крым, Севастополь")
    assert compact_tender_region(item) == "Республика Крым, Севастополь"


def test_registry_repairs_eis_highlight_splits_in_organization_name() -> None:
    rows = build_customer_registry(
        [tender(customer="ГБУ ЗАПОРОЖСК ОЙ ОБЛАСТ И", region="Запорожская область")],
        [],
    )

    assert rows[0][1] == "ГБУ ЗАПОРОЖСКОЙ ОБЛАСТИ"


def test_registry_uses_structured_public_tektorg_contacts_without_overwriting_manual_data() -> None:
    item = tender(
        source="tektorg",
        raw_text=(
            "Поставка кондиционеров\n"
            "TEKTORG_INN=9100000001\n"
            "TEKTORG_LEGAL_ADDRESS=Республика Крым, Симферополь\n"
            "TEKTORG_EMAIL=office@example.test\n"
            "TEKTORG_PHONE=+7 978 000-00-00\n"
            "TEKTORG_CONTACT_PERSON=Иванов Иван\n"
            "TEKTORG_CONTACT_SOURCE=https://www.tektorg.ru/44-fz/procedures/101"
        ),
    )

    rows = build_customer_registry([item], [])

    assert rows[0][4] == "9100000001"
    assert rows[0][5] == "Республика Крым, Симферополь"
    assert rows[0][7:10] == ["office@example.test", "+7 978 000-00-00", "Иванов Иван"]
    assert rows[0][11] == "https://www.tektorg.ru/44-fz/procedures/101"
    assert rows[0][14] == "Новый"
