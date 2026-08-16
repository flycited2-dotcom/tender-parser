from __future__ import annotations

from pathlib import Path

from tender_parser.supplier_search import SupplierProduct, _evaluate_product, _search_query, export_supplier_search, search_case_products
from tender_parser.tender_case import LineItem, initialize_case


class FakeGateway:
    def search(self, query: str, *, limit: int = 10) -> tuple[int, list[SupplierProduct]]:
        assert query == "Ноутбук Lenovo ThinkBook 16"
        assert limit == 5
        return (
            1,
            [
                SupplierProduct(
                    sku="10539750",
                    name="Lenovo ThinkBook 16 G7",
                    purchase_price_gross=72000.0,
                    stock_status="available",
                    is_available=True,
                    delivery_days=7,
                    vendor="Lenovo",
                    part="21MW0001RU",
                    product_url="https://shop.example/product/thinkbook-16",
                )
            ],
        )


def test_supplier_search_creates_conditional_draft_without_touching_offers(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases" / "supplier-1"
    initialize_case(case_dir, case_id="supplier-1", title="Ноутбуки")
    (case_dir / "items.csv").write_text(
        "line_id;name;quantity;unit;required_specs;mandatory\n"
        "1;Ноутбук Lenovo ThinkBook 16;2;шт.;RAM не менее 16 ГБ;yes\n",
        encoding="utf-8-sig",
    )
    original_offers = (case_dir / "offers.csv").read_bytes()

    results = search_case_products(case_dir, FakeGateway(), limit_per_item=5)
    outputs = export_supplier_search(results, case_dir / "output")

    assert len(results) == 1
    assert results[0].products[0].sku == "10539750"
    assert (case_dir / "offers.csv").read_bytes() == original_offers
    draft = outputs["offers"].read_text(encoding="utf-8-sig")
    assert "10539750" in draft
    assert ";conditional;no;" in draft
    assert "окончательное соответствие подтверждается" in draft


def test_supplier_search_records_per_line_error_and_continues(tmp_path: Path) -> None:
    class BrokenGateway:
        def search(self, query: str, *, limit: int = 10):
            raise ValueError("catalog unavailable")

    case_dir = tmp_path / "cases" / "supplier-error"
    initialize_case(case_dir, case_id="supplier-error", title="Ноутбуки")
    (case_dir / "items.csv").write_text(
        "line_id;name;quantity;unit;required_specs;mandatory\n1;Ноутбук;1;шт.;;yes\n",
        encoding="utf-8-sig",
    )

    results = search_case_products(case_dir, BrokenGateway())

    assert len(results) == 1
    assert results[0].products == []
    assert results[0].error == "catalog unavailable"


def test_supplier_search_compares_numeric_requirements_conservatively(tmp_path: Path) -> None:
    class AttributeGateway:
        def search(self, query: str, *, limit: int = 10):
            return (
                2,
                [
                    SupplierProduct(
                        sku="PASS",
                        name="Ноутбук 16/512",
                        purchase_price_gross=70000,
                        stock_status="available",
                        is_available=True,
                        delivery_days=7,
                        attributes=(
                            {"key": "ram", "label": "Оперативная память", "value": "16 ГБ", "numericValue": 16, "unit": "ГБ"},
                            {"key": "storage_capacity", "label": "Объем накопителя", "value": "1 ТБ", "numericValue": 1, "unit": "ТБ"},
                        ),
                    ),
                    SupplierProduct(
                        sku="FAIL",
                        name="Ноутбук 8/256",
                        purchase_price_gross=50000,
                        stock_status="available",
                        is_available=True,
                        delivery_days=7,
                        attributes=(
                            {"key": "ram", "label": "Оперативная память", "value": "8 ГБ", "numericValue": 8, "unit": "ГБ"},
                            {"key": "storage_capacity", "label": "Объем накопителя", "value": "256 ГБ", "numericValue": 256, "unit": "ГБ"},
                        ),
                    ),
                ],
            )

    case_dir = tmp_path / "cases" / "compare"
    initialize_case(case_dir, case_id="compare", title="Ноутбуки")
    (case_dir / "items.csv").write_text(
        "line_id;name;quantity;unit;required_specs;mandatory\n"
        '1;Ноутбук;1;шт.;"Оперативная память не менее 16 ГБ; Объем накопителя не менее 512 ГБ";yes\n',
        encoding="utf-8-sig",
    )

    results = search_case_products(case_dir, AttributeGateway())

    assert results[0].products[0].compliance_status == "compliant"
    assert [check.status for check in results[0].products[0].compliance_checks] == ["pass", "pass"]
    assert results[0].products[1].compliance_status == "not_compliant"
    assert [check.status for check in results[0].products[1].compliance_checks] == ["fail", "fail"]


def test_supplier_query_removes_okpd_and_adds_compatible_device_model() -> None:
    item = LineItem(
        line_id="1",
        name="Блок фотобарабана 26.20.40.120",
        quantity=1,
        unit="шт.",
        required_specs="Совместимость с принтером Brother HL-L5210DW: Да; Ресурс не менее 57 000 листов",
        mandatory=True,
    )

    assert _search_query(item) == "Фотобарабан HL-L5210DW"


def test_duplicate_queries_share_one_gateway_call(tmp_path: Path) -> None:
    class CountingGateway:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, *, limit: int = 10):
            self.calls += 1
            return 1, [SupplierProduct("1", "Ноутбук", 1000, "available", True, 1)]

    case_dir = tmp_path / "cases" / "cache"
    initialize_case(case_dir, case_id="cache", title="Ноутбуки")
    (case_dir / "items.csv").write_text(
        "line_id;name;quantity;unit;required_specs;mandatory\n"
        "1;Ноутбук;1;шт.;;yes\n"
        "2;Ноутбук;2;шт.;;yes\n",
        encoding="utf-8-sig",
    )
    gateway = CountingGateway()

    results = search_case_products(case_dir, gateway)

    assert len(results) == 2
    assert gateway.calls == 1


def test_fuser_drive_is_not_accepted_as_complete_fuser() -> None:
    item = LineItem(
        "4",
        "Термоблок",
        2,
        "шт.",
        "Фьюзер (печка) в сборе для Kyocera M2135dn: Да",
        True,
    )
    product = SupplierProduct(
        "9933768",
        "Привод термоблока Kyocera 302RV94020 для M2135dn",
        6134.63,
        "available",
        True,
        0,
    )

    evaluated = _evaluate_product(item, product)

    assert evaluated.compliance_status == "not_compliant"
    assert evaluated.compliance_checks[0].status == "fail"
