from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests

from tender_parser.control_center import _handler_for
from tender_parser.tender_case import initialize_case


def test_control_center_renders_existing_case_and_accepts_upload(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases" / "existing"
    initialize_case(case_dir, case_id="existing", title="Поставка МФУ")
    (case_dir / "items.csv").write_text(
        "line_id;name;quantity;unit;required_specs;mandatory\n1;МФУ;1;шт.;A4;yes\n",
        encoding="utf-8-sig",
    )
    (case_dir / "output" / "supplier_candidates.json").write_text(
        json.dumps(
            [
                {
                    "line_id": "1",
                    "products": [
                        {
                            "sku": "MFP-1",
                            "name": "МФУ A4",
                            "purchase_price_gross": 6000,
                            "stock_status": "available",
                            "is_available": True,
                            "delivery_days": 3,
                            "product_url": "",
                            "compliance_status": "compliant",
                            "compliance_checks": [],
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        page = requests.get(url, timeout=5)
        assert page.status_code == 200
        assert "Тендерный агент" in page.text
        assert "Поставка МФУ" in page.text
        assert "Выбрать" in page.text

        supplier = requests.post(
            f"{url}/api/suppliers",
            json={"name": "Тест-поставщик", "email": "sales@example.test", "categories": "МФУ"},
            timeout=5,
        )
        assert supplier.status_code == 201
        assert requests.get(f"{url}/api/suppliers", timeout=5).json()["suppliers"][0]["name"] == "Тест-поставщик"

        selected = requests.post(
            f"{url}/api/cases/existing/offers/select",
            json={"line_id": "1", "sku": "MFP-1"},
            timeout=5,
        )
        assert selected.status_code == 200
        assert selected.json()["economics"]["procurement_gross"] == "6000.00"

        economics = requests.post(
            f"{url}/api/cases/existing/economics",
            json={"nmck": "15000", "delivery_cost": "1000", "region": "Симферополь"},
            timeout=5,
        )
        assert economics.status_code == 200
        assert economics.json()["economics"]["target_price"] == "8800.00"

        response = requests.post(
            f"{url}/api/cases",
            data={"tender_number": "0174500001126009999", "title": "Комплектующие", "law": "44-FZ"},
            files={"documents": ("ТЗ.txt", b"technical specification")},
            timeout=5,
        )
        assert response.status_code == 201
        created = response.json()["case_id"]
        assert created == "0174500001126009999"
        assert (tmp_path / "cases" / created / "documents" / "ТЗ.txt").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
