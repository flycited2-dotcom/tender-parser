import json
from pathlib import Path

from openpyxl import Workbook

from tender_parser.case_workflow import (
    clear_selected_offer,
    load_case_dashboard,
    run_case_workflow,
    select_supplier_candidate,
    update_case_economics,
)
from tender_parser.supplier_search import SupplierProduct
from tender_parser.tender_case import initialize_case, load_case


class WorkflowGateway:
    def search(self, query: str, *, limit: int = 10):
        return (
            1,
            [
                SupplierProduct(
                    sku="DR-1",
                    name="Фотобарабан DR-1 для Printer X, 60000 стр.",
                    purchase_price_gross=1000,
                    stock_status="available",
                    is_available=True,
                    delivery_days=3,
                )
            ],
        )


def test_one_workflow_promotes_items_and_creates_supplier_and_alternative_outputs(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases" / "workflow-1"
    initialize_case(case_dir, case_id="workflow-1", title="1234567890123456789")
    documents = case_dir / "documents"
    (documents / "notice.txt").write_text("Извещение об осуществлении закупки. НМЦК 130 000 руб.", encoding="utf-8")
    (documents / "contract.txt").write_text("Проект контракта. Оплата в течение 7 рабочих дней.", encoding="utf-8")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Техническое задание"
    sheet.append(["№", "Наименование товара", "Количество", "Единица измерения", "Характеристики"])
    sheet.append([1, "Фотобарабан", 2, "шт.", "Ресурс не менее 57000 листов"])
    workbook.save(documents / "spec.xlsx")

    summary = run_case_workflow(case_dir, gateway=WorkflowGateway())
    tender_case, items, _, _ = load_case(case_dir)
    dashboard = load_case_dashboard(case_dir)

    assert summary["status"] == "completed"
    assert summary["items_promoted"] is True
    assert tender_case.nmck is not None
    assert len(items) == 1
    assert dashboard["supplier"]
    assert (case_dir / "output" / "alternative_search.json").exists()
    assert (case_dir / "output" / "supplier_requests.csv").exists()


def test_candidate_selection_persists_offer_and_recalculates_case(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases" / "selection-1"
    initialize_case(case_dir, case_id="selection-1", title="Поставка фотобарабанов")
    (case_dir / "items.csv").write_text(
        "line_id;name;quantity;unit;required_specs;mandatory\n1;Фотобарабан;2;шт.;Ресурс 57000;yes\n",
        encoding="utf-8-sig",
    )
    (case_dir / "output" / "supplier_candidates.json").write_text(
        json.dumps(
            [
                {
                    "line_id": "1",
                    "products": [
                        {
                            "sku": "DR-1",
                            "name": "Фотобарабан DR-1",
                            "purchase_price_gross": 1000,
                            "stock_status": "available",
                            "is_available": True,
                            "delivery_days": 3,
                            "product_url": "https://example.test/dr-1",
                            "compliance_status": "conditional",
                            "compliance_checks": [
                                {"requirement": "Ресурс", "status": "pass", "reason": "Указан в карточке"}
                            ],
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = select_supplier_candidate(case_dir, line_id="1", sku="DR-1")
    _, _, offers, _ = load_case(case_dir)

    assert result["economics"]["selected_count"] == 1
    assert result["economics"]["procurement_gross"] == "2000.00"
    assert offers[0].selected is True
    assert offers[0].compliance_status == "conditional"
    assert (case_dir / "output" / "case_report.xlsx").exists()

    recalculated = update_case_economics(
        case_dir,
        {"nmck": "5000", "planned_bid": "4500", "delivery_cost": "500", "region": "Симферополь"},
    )
    assert recalculated["economics"]["expenses_gross"] == "500.00"
    assert recalculated["economics"]["target_price"] == "3100.00"
    assert recalculated["economics"]["assessment_price"] == "4500"

    cleared = clear_selected_offer(case_dir, line_id="1")
    assert cleared["cleared"] is True
    assert cleared["economics"]["selected_count"] == 0
