from pathlib import Path

from openpyxl import Workbook

from tender_parser.case_workflow import load_case_dashboard, run_case_workflow
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
