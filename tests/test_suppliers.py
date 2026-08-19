from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from tender_parser.models import TenderRecord
from tender_parser.suppliers import (
    SupplierCatalog,
    export_supplier_matches,
    format_supplier_matches,
)


def _catalog(tmp_path: Path) -> SupplierCatalog:
    catalog_dir = tmp_path / "supplier_catalog"
    inbox = catalog_dir / "private" / "promet" / "inbox"
    inbox.mkdir(parents=True)
    (catalog_dir / "suppliers.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suppliers": [
                    {
                        "id": "promet",
                        "name": "ПРОМЕТ",
                        "enabled": True,
                        "file_globs": ["private/promet/inbox/*.xlsx"],
                        "tender_categories": ["Офисная, архивная и складская мебель"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    workbook = Workbook()
    base = workbook.active
    base.title = "база"
    base.append(["Артикул", "Наименование", "ID", "Розничная цена", "Статус"])
    base.append(["S001", "Шкаф архивный ШХА-850", 101, 20_000, "D"])
    base.append(["S002", "Стеллаж MS Standart 185", 102, 10_000, "D"])
    base.append(["S003", "Коромысло для замка к шкафам", 103, 100, "D"])

    detailed = workbook.create_sheet("Шкафы")
    for _ in range(5):
        detailed.append([])
    detailed.append(
        [
            "ID",
            "Артикул",
            "Группа",
            "Наименование",
            "Розничная цена",
            "Ваша оптовая цена",
        ]
    )
    detailed.append([101, "S001", "A", "Шкаф архивный ШХА-850", 20_000, 14_600])
    workbook.save(inbox / "promet.xlsx")
    return SupplierCatalog(catalog_dir)


def test_catalog_builds_private_index_and_prefers_dealer_price(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)

    status = catalog.refresh(force=True)

    assert status.status == "ok"
    assert status.product_count == 3
    assert catalog.index_path.is_file()
    cabinet = next(item for item in catalog.products if item.article == "S001")
    assert cabinet.dealer_price == 14_600
    assert cabinet.retail_price == 20_000
    assert cabinet.category == "Шкафы"


def test_catalog_search_matches_inflections_models_and_articles(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    catalog.refresh(force=True)

    by_name = catalog.search("Поставка архивных шкафов", limit=5)
    by_model = catalog.search("MS Standart 185", limit=5)
    by_article = catalog.search("S001", limit=5)

    assert by_name[0].product.article == "S001"
    assert by_model[0].product.article == "S002"
    assert by_article[0].product.article == "S001"
    assert by_article[0].score > by_name[0].score


def test_catalog_respects_tender_category_and_exports_sidecar(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    catalog.refresh(force=True)
    matching = TenderRecord(
        title="Поставка архивных шкафов",
        url="https://example.test/1",
        source="test",
        tender_number="1",
        category="Офисная, архивная и складская мебель",
        matched_terms=["шкаф архивный"],
        deadline=datetime(2026, 9, 1),
    )
    unrelated = TenderRecord(
        title="Поставка архивных шкафов",
        url="https://example.test/2",
        source="test",
        tender_number="2",
        category="Компьютерная техника и периферия",
    )

    output, count = export_supplier_matches(
        catalog, [matching, unrelated], tmp_path / "exports" / "supplier_matches.json"
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert count == 1
    assert payload["items"][0]["tender_number"] == "1"
    assert payload["items"][0]["candidates"][0]["dealer_price"] == 14_600


def test_format_supplier_matches_is_ready_for_telegram(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    catalog.refresh(force=True)
    text = format_supplier_matches("шкаф архивный", catalog.search("шкаф архивный"))

    assert "ПРОМЕТ" in text
    assert "S001" in text
    assert "дилерская" in text
    assert "14 600.00 ₽" in text
