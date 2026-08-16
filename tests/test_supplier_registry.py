from pathlib import Path

from tender_parser.supplier_registry import (
    REQUEST_HEADERS,
    add_supplier,
    assign_supplier_request,
    list_supplier_requests,
    list_suppliers,
    update_supplier_request,
)
from tender_parser.tender_case import initialize_case


def test_supplier_registry_assigns_multiple_rfq_and_tracks_response(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases" / "rfq-1"
    initialize_case(case_dir, case_id="rfq-1", title="Поставка")
    request_path = case_dir / "output" / "supplier_requests.csv"
    request_path.write_text(
        ";".join(REQUEST_HEADERS)
        + "\n1;required;Чип;10;шт.;TN3600;;;;Текст;;не отправлен;;;;\n",
        encoding="utf-8-sig",
    )
    first = add_supplier(tmp_path, {"name": "Альфа", "email": "sales@alpha.test", "categories": "ЗИП"})
    second = add_supplier(tmp_path, {"name": "Бета", "website": "https://beta.test"})

    assert len(list_suppliers(tmp_path)) == 2
    assigned_first = assign_supplier_request(
        tmp_path, case_dir, line_id="1", supplier_id=first["supplier_id"]
    )
    assigned_second = assign_supplier_request(
        tmp_path, case_dir, line_id="1", supplier_id=second["supplier_id"]
    )
    assert assigned_first["response_status"] == "подготовлен"
    assert assigned_second["supplier"] == "Бета"
    assert len(list_supplier_requests(case_dir)) == 2

    updated = update_supplier_request(
        case_dir,
        line_id="1",
        supplier="Альфа",
        values={"response_status": "ответ получен", "response_price": "1250", "response_stock": "10 шт."},
    )
    assert updated["response_price"] == "1250"
    assert updated["response_status"] == "ответ получен"
