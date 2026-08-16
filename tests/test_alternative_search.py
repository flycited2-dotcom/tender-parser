from decimal import Decimal
from pathlib import Path

from tender_parser.alternative_search import build_alternative_tasks, export_alternative_search
from tender_parser.supplier_search import LineSearchResult, SupplierProduct
from tender_parser.tender_case import LineItem


def test_missing_main_supplier_product_creates_ready_rfq(tmp_path: Path) -> None:
    item = LineItem(
        "2",
        "Чип для тонер-картриджа",
        Decimal("100"),
        "шт.",
        "Brother HL-L5210DW; Ресурс чипа: ≥ 11 000 листов",
        True,
    )
    result = LineSearchResult("2", item.name, item.required_specs, "Чип HL-L5210DW", 0)

    tasks = build_alternative_tasks([item], [result])
    outputs = export_alternative_search(tasks, tmp_path)

    assert tasks[0].status == "required"
    assert "TN3600XXL" in tasks[0].oem_parts
    assert "цену с НДС" in tasks[0].request_text
    assert "не отправлен" in outputs["requests"].read_text(encoding="utf-8-sig")


def test_confirmed_available_product_makes_alternative_a_backup() -> None:
    item = LineItem("1", "Фотобарабан", Decimal("15"), "шт.", "Brother HL-L5210DW", True)
    product = SupplierProduct(
        sku="10803126",
        name="Фотобарабан Sakura DR3600 для HL-L5210DW, 75000 стр.",
        purchase_price_gross=1121.77,
        stock_status="available",
        is_available=True,
        delivery_days=0,
        compliance_status="compliant",
    )
    result = LineSearchResult("1", item.name, item.required_specs, "DR3600", 1, products=[product])

    task = build_alternative_tasks([item], [result])[0]

    assert task.status == "backup"
