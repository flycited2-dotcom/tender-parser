from datetime import datetime

from tender_parser.customers import (
    build_customer_registry,
    compact_tender_region,
    customer_region,
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


def test_registry_skips_private_and_wrong_region_customers() -> None:
    rows = build_customer_registry(
        [
            tender(customer='ООО "Частное"'),
            tender(customer='ГБУ "Чужой регион"', region="Амурская область"),
        ],
        [],
    )
    assert rows == []


def test_compact_region_replaces_long_multiregion_list_with_targets() -> None:
    item = tender(region="Республика Адыгея, Амурская область, Республика Крым, Севастополь")
    assert compact_tender_region(item) == "Республика Крым, Севастополь"


def test_registry_repairs_eis_highlight_splits_in_organization_name() -> None:
    rows = build_customer_registry(
        [tender(customer="ГБУ ЗАПОРОЖСК ОЙ ОБЛАСТ И", region="Запорожская область")],
        [],
    )

    assert rows[0][1] == "ГБУ ЗАПОРОЖСКОЙ ОБЛАСТИ"
